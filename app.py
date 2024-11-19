import os
import threading
import socket
import pickle
from cryptography.fernet import Fernet
from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from werkzeug.utils import secure_filename
from io import BytesIO
import struct
import time
import random
import hashlib

app = Flask(__name__)
app.secret_key = 'your_secret_key'

# Constants
UPLOAD_FOLDER = 'uploads'
COMMON_PORT = 5000  # Common port for all peers
REPLICATION_FACTOR = 3  # Number of replicas for each file chunk

# Ensure the upload folder exists
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

def send_data(client, data):
    serialized_data = pickle.dumps(data)
    # Prepend the length of the serialized data (4 bytes for length)
    message = struct.pack('!I', len(serialized_data)) + serialized_data
    client.sendall(message)

def receive_data(client):
    # Read the first 4 bytes to get the length of the data
    data_length_bytes = client.recv(4)
    if not data_length_bytes:
        return None
    
    data_length = struct.unpack('!I', data_length_bytes)[0]
    
    # Now read the actual data
    data = b''
    while len(data) < data_length:
        packet = client.recv(data_length - len(data))
        if not packet:
            return None
        data += packet

    return pickle.loads(data)

def calculate_hash(data):
        """Calculate the SHA-256 hash of the given data."""
        return hashlib.sha256(data).hexdigest()

# Node class with enhanced functionality
class Node:
    def __init__(self, host, storage_limit):
        self.host = host
        self.port = COMMON_PORT
        self.peers = []  # Each peer is now a tuple: (peer_host, peer_storage_limit, reliability_score)
        self.storage = {}
        self.storage_limit = storage_limit * 1024 * 1024  # Convert MB to bytes
        self.used_storage = 0
        self.encryption_key = Fernet.generate_key()
        self.cipher = Fernet(self.encryption_key)
        self.server_thread = threading.Thread(target=self.start_server)
        self.server_thread.start()

    def update_peer_score(self, peer_host, success=True):
        """Increase or decrease the reliability score of a peer."""
        for idx, (host, limit, score) in enumerate(self.peers):
            if host == peer_host:
                new_score = score + 1 if success else max(score - 1, 0)
                self.peers[idx] = (host, limit, new_score)
                break

    def get_reliable_peers(self):
        """Return a sorted list of peers based on reliability score."""
        return sorted(self.peers, key=lambda peer: peer[2], reverse=True)


    def start_server(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        server.listen(5)
        print(f"Node started on {self.host}:{self.port}")

        try:
            while True:
                client, address = server.accept()
                threading.Thread(target=self.handle_peer, args=(client,)).start()
        finally:
            server.close()


    def handle_peer(self, client):
        try:
            while True:
                received_data = receive_data(client)
                if received_data:
                    command, content = received_data
                    if command == 'JOIN':
                        peer_host, peer_storage_limit = content
                        if peer_host != self.host and peer_host not in [peer[0] for peer in self.peers]:
                            self.peers.append((peer_host, peer_storage_limit))
                            self.broadcast_peers()
                            # Send the current peer list back to the newly joined peer (excluding self)
                            filtered_peers = [(p_host, p_storage) for p_host, p_storage in self.peers if p_host != self.host]
                            send_data(client, ('PEERS', filtered_peers))
                    
                    elif command == 'STORE_CHUNK':
                        port, chunk_id, chunk, filename, timestamp, chunk_size = content
                        if self.used_storage + chunk_size <= self.storage_limit:
                            # Save chunk to disk
                            chunk_folder = os.path.join('peer_storage', self.host)
                            os.makedirs(chunk_folder, exist_ok=True)
                            chunk_path = os.path.join(chunk_folder, chunk_id)

                            with open(chunk_path, 'wb') as chunk_file:
                                chunk_file.write(chunk)

                            # Keep metadata in memory
                            self.storage[chunk_id] = (chunk_path, filename, timestamp)
                            self.used_storage += chunk_size
                            send_data(client, ('CHUNK_STORED', f'Chunk {chunk_id} stored successfully'))
                        else:
                            send_data(client, ('ERROR', 'Insufficient storage space'))

                    elif command == 'RETRIEVE_CHUNK':
                        chunk_id = content
                        if chunk_id in self.storage:
                            # Retrieve chunk from storage
                            chunk_path, filename, timestamp = self.storage[chunk_id]
                            
                            # Read the chunk from disk
                            try:
                                with open(chunk_path, 'rb') as chunk_file:
                                    encrypted_chunk = chunk_file.read()
                                # Send back the data, consider using a larger buffer size if necessary
                                send_data(client, ('CHUNK_DATA', (chunk_id, encrypted_chunk, filename, timestamp)))
                            except FileNotFoundError:
                                print(f'{filename} not in {chunk_path}')
                                send_data(client, ('ERROR', f'Chunk {chunk_id} not found on disk'))
                        else:
                            send_data(client, ('ERROR', f'Chunk {chunk_id} not found in storage'))
                    elif command == 'DELETE_CHUNKS':
                        peer_host = content
                        self.delete_chunks_from_peer(peer_host)
                        print("initiated delete protocol")
                    elif command == 'PEERS':
                        updated_peers = [peer for peer in content if peer[0] != self.host]
                        self.peers = updated_peers
                        print(f"Updated peers (excluding self): {self.peers}")
                        flash('Peer list updated')
        except Exception as e:
            print(f"Error handling peer: {e}")
        finally:
            client.close()

    def connect_to_network(self, peer_host, peer_storage_limit):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                client.connect((peer_host, COMMON_PORT))
                send_data(client, ('JOIN', (self.host, peer_storage_limit)))
                
                # Request the current list of peers from the connected network
                response_data = receive_data(client)
                if response_data:
                    command, content = response_data
                    if command == 'PEERS':
                        # Update this peer's list with the received list (excluding self)
                        self.peers = [peer for peer in content if peer[0] != self.host]
                        print(f"Received updated peer list (excluding self): {self.peers}")
                        self.broadcast_peers()  # Inform other peers of this peer's existence

            # Add the connected peer itself
            if (peer_host, peer_storage_limit) not in self.peers and peer_host != self.host:
                self.peers.append((peer_host, peer_storage_limit))
            flash('Connected to existing network successfully')

        except (ConnectionRefusedError, socket.timeout):
            flash('Failed to connect to existing network.')

    def broadcast_peers(self):
        """Send the updated list of peers to each connected peer (excluding self)."""
        for peer_host, peer_storage_limit in self.peers:
            if peer_host != self.host:  # Avoid sending to itself
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                    try:
                        client.connect((peer_host, COMMON_PORT))
                        # Send the peer list, excluding the current node itself
                        filtered_peers = [(p_host, p_storage) for p_host, p_storage in self.peers if p_host != peer_host]
                        send_data(client, ('PEERS', filtered_peers))
                    except Exception as e:
                        print(f"Error broadcasting to peer {peer_host}: {str(e)}")

    
    
    def check_and_replicate_chunks(self):
        """Check if each chunk has enough replicas, if not, replicate it."""
        for chunk_id, (chunk_path, filename, timestamp) in self.storage.items():
            # Count how many peers have this chunk
            replica_count = 0
            for peer_host, _, _ in self.peers:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                    try:
                        client.connect((peer_host, COMMON_PORT))
                        send_data(client, ('RETRIEVE_CHUNK', chunk_id))
                        response_data = receive_data(client)
                        if response_data and response_data[0] == 'CHUNK_DATA':
                            replica_count += 1
                    except:
                        continue

            # If under-replicated, replicate the chunk to other peers
            if replica_count < REPLICATION_FACTOR:
                with open(chunk_path, 'rb') as chunk_file:
                    chunk = chunk_file.read()
                    for _ in range(REPLICATION_FACTOR - replica_count):
                        # Choose a reliable peer to store the additional replica
                        chosen_peer = random.choice(self.get_reliable_peers())
                        peer_host = chosen_peer[0]
                        try:
                            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                                client.connect((peer_host, COMMON_PORT))
                                send_data(client, ('STORE_CHUNK', (self.port, chunk_id, chunk, filename, timestamp, len(chunk))))
                                print(f"Replicated {chunk_id} to {peer_host}")
                        except Exception as e:
                            print(f"Error replicating chunk {chunk_id} to {peer_host}: {str(e)}")

    
    def store_file(self, filepath):
        try:
            with open(filepath, 'rb') as file:
                file_data = file.read()
            encrypted_file = self.cipher.encrypt(file_data)
            filename = os.path.basename(filepath)
            file_size = len(encrypted_file)
            timestamp = time.time()

            # Split file into chunks
            chunk_size = 4096  # 4 KB chunks
            chunks = [encrypted_file[i:i + chunk_size] for i in range(0, len(encrypted_file), chunk_size)]
            chunk_ids = [f"{filename}_chunk_{i}" for i in range(len(chunks))]

            for chunk_id, chunk in zip(chunk_ids, chunks):
                for _ in range(REPLICATION_FACTOR):
                    # Exclude the current host from available peers
                    available_peers = [peer for peer in self.peers if peer[0] != self.host]

                    if not available_peers:
                        flash('No other peers available for storage')
                        return

                    # Randomly select a peer to store each chunk
                    chosen_peer = random.choice(available_peers)
                    peer_host = chosen_peer[0]

                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                        try:
                            client.connect((peer_host, COMMON_PORT))
                            send_data(client, ('STORE_CHUNK', (self.port, chunk_id, chunk, filename, timestamp, len(chunk))))

                            # Receive acknowledgment from the peer
                            response_data = receive_data(client)
                            if response_data:
                                response, message = response_data
                                if response == 'CHUNK_STORED':
                                    print(f"Successfully stored {chunk_id} on {peer_host}")
                                elif response == 'ERROR':
                                    flash(f"Failed to store chunk {chunk_id} on peer {peer_host}: {message}")
                            else:
                                flash(f"No response from peer {peer_host} for chunk {chunk_id}")
                        except Exception as e:
                            flash(f"Error storing chunk {chunk_id} on peer {peer_host}: {str(e)}")

            self.used_storage += file_size
            os.remove(filepath)
            flash(f'File {filename} stored successfully across the network in chunks')
        except Exception as e:
            flash(f'Failed to store file: {str(e)}')



    def retrieve_file(self, filename):
        """Retrieve and reassemble a file from chunks."""
        retrieved_chunks = []
        chunk_id_index = 0  # Start with the first chunk
        missing_chunk = False  # Flag to indicate if a chunk is missing

        while not missing_chunk:
            chunk_id = f"{filename}_chunk_{chunk_id_index}"
            found_chunk = False

            # Check each peer for the current chunk
            for peer_host, _ in self.peers:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                    try:
                        client.settimeout(15)  # Set a timeout to avoid indefinite waiting
                        client.connect((peer_host, COMMON_PORT))
                        send_data(client, ('RETRIEVE_CHUNK', chunk_id))

                        # Properly receive the response using receive_data
                        response_data = receive_data(client)
                        if response_data is None:
                            print(f"No valid data received from {peer_host} for chunk {chunk_id}")
                            continue

                        response, content = response_data

                        if response == 'CHUNK_DATA':
                            chunk_id, encrypted_chunk, filename, timestamp = content
                            retrieved_chunks.append((chunk_id, encrypted_chunk))
                            found_chunk = True
                            break  # Break if the chunk is successfully found
                        elif response == 'ERROR':
                            print(f"Received error for chunk {chunk_id} from {peer_host}: {content}")
                            continue
                        else:
                            print(f"Received unexpected response from {peer_host}: {response}")

                    except socket.timeout:
                        print(f"Timeout while retrieving chunk {chunk_id} from {peer_host}")
                    except Exception as e:
                        print(f"Error retrieving chunk {chunk_id} from {peer_host}: {str(e)}")
                    finally:
                        client.close()

                if found_chunk:
                    break

            # If the chunk was not found on any peer, assume we've retrieved all available chunks
            if not found_chunk:
                missing_chunk = True

            # Move to the next chunk ID
            chunk_id_index += 1

        # If no chunks were found, return a failure message
        if not retrieved_chunks:
            flash('Failed to retrieve file')
            return None, None

        # Sort chunks by chunk_id
        retrieved_chunks.sort(key=lambda x: int(x[0].split('_chunk_')[-1]))

        # Reassemble and decrypt the file
        encrypted_file = b''.join(chunk for _, chunk in retrieved_chunks)
        decrypted_file = self.cipher.decrypt(encrypted_file)
        return decrypted_file, filename


    def calculate_total_network_storage(self):
        """Calculate the total available storage across all peers."""
        return sum(peer_storage for _, peer_storage in self.peers) + (self.storage_limit - self.used_storage) / (1024 * 1024)
    
    
    
    def delete_chunks_from_peer(self, peer_host):
        """Delete all chunks that belong to the specified peer."""
        chunk_folder = os.path.join('peer_storage', peer_host)
        
        # Check if the directory exists
        if os.path.exists(chunk_folder):
            for filename in os.listdir(chunk_folder):
                file_path = os.path.join(chunk_folder, filename)
                if os.path.isfile(file_path):
                    try:
                        os.remove(file_path)
                        print(f"Deleted chunk from {peer_host}: {file_path}")
                    except Exception as e:
                        print(f"Error deleting chunk {file_path}: {str(e)}")
            
            # Remove the directory if empty
            try:
                os.rmdir(chunk_folder)
                print(f"Deleted peer chunk folder: {chunk_folder}")
            except OSError:
                # Directory isn't empty or another error occurred
                pass

    def notify_delete_chunks(self):
        """Notify all connected peers to delete chunks related to this peer."""
        for peer_host, _ in self.peers:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                try:
                    client.connect((peer_host, COMMON_PORT))
                    send_data(client, ('DELETE_CHUNKS', self.host))
                except Exception as e:
                    print(f"Error notifying peer {peer_host} to delete chunks: {str(e)}")



    def leave_network(self):
        """Leave the network, delete all local chunks, and notify peers to delete stored data."""
        # Notify other peers to delete chunks associated with this peer
        self.notify_delete_chunks()

        # Clear local storage
        self.storage.clear()
        self.used_storage = 0

        # Delete all files in the chunk folder specific to this peer
        chunk_folder = os.path.join('peer_storage', self.host)
        if os.path.exists(chunk_folder):
            for filename in os.listdir(chunk_folder):
                file_path = os.path.join(chunk_folder, filename)
                if os.path.isfile(file_path):
                    try:
                        os.remove(file_path)
                        print(f"Deleted file: {file_path}")
                    except Exception as e:
                        print(f"Error deleting file {file_path}: {str(e)}")

            # Optionally remove the entire folder if it's empty
            try:
                os.rmdir(chunk_folder)
                print(f"Deleted chunk folder: {chunk_folder}")
            except OSError:
                # Folder isn't empty or other error occurred, ignore it
                pass

        flash('Left the network and deleted all stored data')


# Initialize Node with the current host IP
current_host = "192.168.247.219" #socket.gethostbyname(socket.gethostname())
node = None

@app.route('/')
def index():
    global node
    total_storage = node.calculate_total_network_storage() if node else 0
    return render_template('index.html', host_ip=node.host if node else current_host, peers=node.peers if node else [], total_storage=total_storage)

@app.route('/connect', methods=['POST'])
def connect():
    global node
    action = request.form['action']
    storage_limit = int(request.form['storage_limit'])
    node = Node(current_host, storage_limit)

    if action == 'create':
        flash(f"Network created with {storage_limit} MB storage. Your IP address is {node.host}")
    elif action == 'join':
        peer_host = request.form['peer_host']
        node.connect_to_network(peer_host, storage_limit)
    return redirect(url_for('index'))

@app.route('/store', methods=['POST'])
def store():
    if not node:
        flash("Please create or join a network first.")
        return redirect(url_for('index'))

    file = request.files['file']
    if file:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
        file.save(filepath)
        node.store_file(filepath)
    else:
        flash('No file selected')
    return redirect(url_for('index'))

@app.route('/retrieve', methods=['POST'])
def retrieve():
    if not node:
        flash("Please join or create a network first.")
        return redirect(url_for('index'))

    # Check if the filename was provided
    filename = request.form.get('filename')
    if not filename:
        flash('Filename is required')
        return redirect(url_for('index'))

    file_data, filename = node.retrieve_file(filename)
    if file_data:
        file_stream = BytesIO(file_data)
        file_stream.seek(0)
        download_filename = f"{filename}"
        return send_file(file_stream, download_name=download_filename, as_attachment=True)
    else:
        flash('Failed to retrieve file')
        return redirect(url_for('index'))



@app.route('/leave', methods=['POST'])
def leave():
    if node:
        node.leave_network()
    flash('Left the network and deleted data')
    return redirect(url_for('index'))

if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 3050
    app.run(debug=True, port=port)

