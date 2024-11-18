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

# Node class with enhanced functionality
class Node:
    def __init__(self, host, storage_limit):
        self.host = host
        self.port = COMMON_PORT
        self.peers = []
        self.storage = {}
        self.storage_limit = storage_limit * 1024 * 1024  # Convert MB to bytes
        self.used_storage = 0  # Track used storage space
        self.encryption_key = Fernet.generate_key()
        self.cipher = Fernet(self.encryption_key)
        self.server_thread = threading.Thread(target=self.start_server)
        self.server_thread.start()

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
                        if peer_host not in [peer[0] for peer in self.peers]:
                            self.peers.append((peer_host, peer_storage_limit))
                            self.broadcast_peers()
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
                    elif command == 'PEERS':
                        self.peers = content
        except Exception as e:
            print(f"Error handling peer: {e}")
        finally:
            client.close()




    def connect_to_network(self, peer_host, peer_storage_limit):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                client.connect((peer_host, COMMON_PORT))
                client.send(pickle.dumps(('JOIN', (self.host, peer_storage_limit))))
            self.peers.append((peer_host, peer_storage_limit))
            flash('Connected to existing network successfully')
        except (ConnectionRefusedError, socket.timeout):
            flash('Failed to connect to existing network.')

    def broadcast_peers(self):
        for peer_host, peer_storage_limit in self.peers:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                try:
                    client.connect((peer_host, COMMON_PORT))
                    send_data(client, ('PEERS', self.peers))
                except:
                    pass


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
                    # Randomly select a peer to store each chunk
                    chosen_peer = random.choice(self.peers) if self.peers else (self.host, self.storage_limit)
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
            flash(f'File {filename} stored successfully across network in chunks')
        except Exception as e:
            flash(f'Failed to store file: {str(e)}')


    def retrieve_file(self, filename):
        """Retrieve and reassemble a file from chunks."""
        chunk_ids = [f"{filename}_chunk_{i}" for i in range(1000)]  # Assuming a reasonable max chunk limit
        retrieved_chunks = []

        for chunk_id in chunk_ids:
            found_chunk = False
            for peer_host, _ in self.peers:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                    try:
                        client.connect((peer_host, COMMON_PORT))
                        client.send(pickle.dumps(('RETRIEVE_CHUNK', chunk_id)))
                        response, content = pickle.loads(client.recv(4096))

                        if response == 'CHUNK_DATA':
                            chunk_id, encrypted_chunk, filename, timestamp = content
                            retrieved_chunks.append((chunk_id, encrypted_chunk))
                            found_chunk = True
                            break
                    except:
                        pass
            if not found_chunk:
                flash(f"Failed to retrieve chunk {chunk_id}")
                return None, None, None

        # Sort chunks by chunk_id
        retrieved_chunks.sort(key=lambda x: int(x[0].split('_chunk_')[-1]))

        # Reassemble and decrypt the file
        encrypted_file = b''.join(chunk for _, chunk in retrieved_chunks)
        decrypted_file = self.cipher.decrypt(encrypted_file)
        return decrypted_file, filename, timestamp

    def calculate_total_network_storage(self):
        """Calculate the total available storage across all peers."""
        return sum(peer_storage for _, peer_storage in self.peers) + (self.storage_limit - self.used_storage) / (1024 * 1024)

    def leave_network(self):
        self.storage.clear()
        upload_folder = app.config['UPLOAD_FOLDER']
        for filename in os.listdir(upload_folder):
            file_path = os.path.join(upload_folder, filename)
            if os.path.isfile(file_path):
                os.remove(file_path)
                print(f"Deleted file: {file_path}")

# Initialize Node with the current host IP
current_host = socket.gethostbyname(socket.gethostname())
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

    file_data, filename, timestamp = node.retrieve_file(filename)
    if file_data:
        file_stream = BytesIO(file_data)
        file_stream.seek(0)
        download_filename = f"{filename}_{time.strftime('%Y%m%d-%H%M%S', time.localtime(timestamp))}"
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

