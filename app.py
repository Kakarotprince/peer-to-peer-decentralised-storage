import os
import threading
import socket
import pickle
from cryptography.fernet import Fernet
from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from werkzeug.utils import secure_filename
from io import BytesIO
import struct  # To send and receive file size

app = Flask(__name__)
app.secret_key = 'your_secret_key'

# Create a directory for temporary file storage
UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Node class
class Node:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.peers = []
        self.storage = {}
        self.cipher = Fernet(Fernet.generate_key())
        self.server_thread = threading.Thread(target=self.start_server)
        self.server_thread.start()

    def start_server(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        server.listen(5)
        try:
            while True:
                client, address = server.accept()
                threading.Thread(target=self.handle_peer, args=(client,)).start()
        finally:
            server.close()

    def handle_peer(self, client):
        try:
            while True:
                data = client.recv(4096)
                if data:
                    command, content = pickle.loads(data)
                    if command == 'STORE':
                        peer_id, encrypted_file, filename = content
                        self.storage[peer_id] = (encrypted_file, filename)
                    elif command == 'RETRIEVE':
                        peer_id = content
                        if peer_id in self.storage:
                            encrypted_file, filename = self.storage[peer_id]
                            # Send file size first
                            file_size = len(encrypted_file)
                            client.send(struct.pack('Q', file_size))  # Send file size (Q: unsigned long long)
                            client.sendall(encrypted_file)  # Send the encrypted file
                            client.send(pickle.dumps(('DATA', filename)))  # Send the filename
                        else:
                            client.send(pickle.dumps(('ERROR', 'File not found')))
        finally:
            client.close()

    def join_network(self, peer_host, peer_port):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                client.settimeout(5)
                client.connect((peer_host, peer_port))
            self.peers.append((peer_host, peer_port))
            flash('Joined network successfully')
        except (ConnectionRefusedError, socket.timeout):
            flash('Failed to connect to peer. Please check the IP and port.')

    def store_file(self, peer_host, peer_port, filepath):
        try:
            print(f"Storing file to {peer_host}:{peer_port}")
            with open(filepath, 'rb') as file:
                file_data = file.read()
            encrypted_file = self.cipher.encrypt(file_data)
            filename = os.path.basename(filepath)
            
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                client.connect((peer_host, peer_port))
                # Inform the peer to store the file
                client.send(pickle.dumps(('STORE', (self.port, encrypted_file, filename))))
            
            flash(f'File {filename} stored successfully on peer {peer_host}:{peer_port}')
        except Exception as e:
            flash(f'Failed to store file: {str(e)}')


    def retrieve_file(self, peer_host, peer_port):
        try:
            print(f"Retrieving file from {peer_host}:{peer_port}")
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                client.connect((peer_host, peer_port))
                client.send(pickle.dumps(('RETRIEVE', self.port)))

                # First receive the file size
                file_size_data = client.recv(8)  # 8 bytes for 'Q' format (unsigned long long)
                if not file_size_data:
                    return None, None
                
                file_size = struct.unpack('Q', file_size_data)[0]  # Unpack the file size

                # Receive the file data in chunks based on file_size
                data = b''
                while len(data) < file_size:
                    packet = client.recv(4096)
                    if not packet:
                        break
                    data += packet

                # Decrypt the received file data
                decrypted_file = self.cipher.decrypt(data)

                # Receive the filename
                filename_pickle = client.recv(4096)
                filename = pickle.loads(filename_pickle)[1]

                return decrypted_file, filename
        except Exception as e:
            print(f"Error during retrieval: {e}")
            return None, None


    def leave_network(self):
        try:
             # Clear in-memory storage dictionary
            self.storage.clear()
            # Delete all files in the uploads directory
            upload_folder = app.config['UPLOAD_FOLDER']
            for filename in os.listdir(upload_folder):
                file_path = os.path.join(upload_folder, filename)
                if os.path.isfile(file_path):
                    os.remove(file_path)
                    print(f"Deleted file: {file_path}")
        except Exception as e:
            flash(f"Error while leaving network: {str(e)}")



# Initialize node
node = Node(host='localhost', port=int(input("Give port address: ")))

# Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/join', methods=['POST'])
def join():
    peer_host = request.form['peer_host']
    peer_port = int(request.form['peer_port'])
    node.join_network(peer_host, peer_port)
    flash('Joined network successfully')
    return redirect(url_for('index'))

@app.route('/store', methods=['POST'])
def store():
    peer_host = request.form['peer_host']
    peer_port = int(request.form['peer_port'])
    file = request.files['file']
    
    if file:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
        file.save(filepath)
        node.store_file(peer_host, peer_port, filepath)
        flash('File stored successfully')
    else:
        flash('No file selected')
    
    return redirect(url_for('index'))

@app.route('/retrieve', methods=['POST'])
def retrieve():
    peer_host = request.form['peer_host']
    peer_port = int(request.form['peer_port'])
    
    try:
        # Retrieve file data and filename from the peer
        file_data, filename = node.retrieve_file(peer_host, peer_port)
        flash(filename)

        if file_data:
            # Create an in-memory file-like object from the decrypted file data
            file_stream = BytesIO(file_data)
            
            # Set the pointer of the BytesIO stream back to the beginning
            file_stream.seek(0)

            # Send the file back as an attachment
            return send_file(file_stream, download_name=filename, as_attachment=True)
        else:
            flash('Failed to retrieve file or file not found')
            return redirect(url_for('index'))
    
    except Exception as e:
        flash(f"Error retrieving file: {str(e)}")
        return redirect(url_for('index'))


@app.route('/leave', methods=['POST'])
def leave():
    node.leave_network()
    flash('Left the network and deleted data')
    return redirect(url_for('index'))

if __name__ == "__main__":
    app.run(debug=True, port=3050)
