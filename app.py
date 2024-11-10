import os
import threading
import socket
import pickle
from cryptography.fernet import Fernet
from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from werkzeug.utils import secure_filename
from io import BytesIO
import struct
import time  # To add timestamp for files

app = Flask(__name__)
app.secret_key = 'your_secret_key'

# Constants
UPLOAD_FOLDER = 'uploads'
COMMON_PORT = 5000  # Common port for all peers

# Ensure the upload folder exists
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Node class
class Node:
    def __init__(self, host):
        self.host = host
        self.port = COMMON_PORT
        self.peers = []
        self.storage = {}
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
                data = client.recv(4096)
                if data:
                    command, content = pickle.loads(data)
                    if command == 'JOIN':
                        peer_host = content
                        if peer_host not in self.peers:
                            self.peers.append(peer_host)
                            # Broadcast updated peer list to all connected peers
                            self.broadcast_peers()
                            flash('New peer added to the network')
                    elif command == 'STORE':
                        peer_id, encrypted_file, filename, timestamp = content
                        self.storage[peer_id] = (encrypted_file, filename, timestamp)
                    elif command == 'RETRIEVE':
                        peer_id = content
                        if peer_id in self.storage:
                            encrypted_file, filename, timestamp = self.storage[peer_id]
                            file_size = len(encrypted_file)
                            client.send(struct.pack('Q', file_size))
                            client.sendall(encrypted_file)
                            client.send(pickle.dumps(('DATA', filename, timestamp)))
                        else:
                            client.send(pickle.dumps(('ERROR', 'File not found')))
                    elif command == 'PEERS':
                        self.peers = content
        finally:
            client.close()

    def connect_to_network(self, peer_host):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                client.connect((peer_host, COMMON_PORT))
                client.send(pickle.dumps(('JOIN', self.host)))
            self.peers.append(peer_host)
            flash('Connected to existing network successfully')
        except (ConnectionRefusedError, socket.timeout):
            flash('Failed to connect to existing network.')

    def broadcast_peers(self):
        """Send the updated peer list to all connected peers."""
        for peer_host in self.peers:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                try:
                    client.connect((peer_host, COMMON_PORT))
                    client.send(pickle.dumps(('PEERS', self.peers)))
                except:
                    pass  # Ignore if peer is unavailable

    def set_encryption_key(self, custom_key=None):
        if custom_key:
            # Use custom key
            self.encryption_key = custom_key.encode()
        else:
            # Generate a random key
            self.encryption_key = Fernet.generate_key()
        self.cipher = Fernet(self.encryption_key)

    def store_file(self, filepath):
        try:
            with open(filepath, 'rb') as file:
                file_data = file.read()
            encrypted_file = self.cipher.encrypt(file_data)
            filename = os.path.basename(filepath)
            timestamp = time.time()  # Capture the upload time

            for peer_host in self.peers:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                    if peer_host != self.host:
                        client.connect((peer_host, COMMON_PORT))
                        client.send(pickle.dumps(('STORE', (self.port, encrypted_file, filename, timestamp))))
            
            # Delete the local file after distribution
            os.remove(filepath)
            flash(f'File {filename} stored successfully across network')
        except Exception as e:
            flash(f'Failed to store file: {str(e)}')

    def retrieve_file(self):
        """Retrieve the file stored by the current node's IP from the network."""
        file_data, filename, timestamp = self.storage.get(self.port, (None, None, None))
        if file_data:
            decrypted_file = self.cipher.decrypt(file_data)
            return decrypted_file, filename, timestamp
        flash("No file found to retrieve.")
        return None, None, None

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
node = Node(host=current_host)

@app.route('/')
def index():
    return render_template('index.html', host_ip=node.host, peers=node.peers)

@app.route('/connect', methods=['POST'])
def connect():
    action = request.form['action']
    if action == 'create':
        flash(f"Network created. Your IP address is {node.host}")
    elif action == 'join':
        peer_host = request.form['peer_host']
        node.connect_to_network(peer_host)
    return redirect(url_for('index'))

@app.route('/set_encryption_key', methods=['POST'])
def set_encryption_key():
    custom_key = request.form.get('custom_key')
    node.set_encryption_key(custom_key)
    flash('Encryption key set successfully.')
    return redirect(url_for('index'))

@app.route('/store', methods=['POST'])
def store():
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
    file_data, filename, timestamp = node.retrieve_file()

    if file_data:
        file_size = len(file_data)
        download_url = url_for('download_file', filename=filename)
        flash(f'File retrieved: {filename} (Size: {file_size} bytes, Uploaded: {time.ctime(timestamp)})')
        return redirect(download_url)
    else:
        flash('Failed to retrieve file')
        return redirect(url_for('index'))

@app.route('/download/<filename>')
def download_file(filename):
    file_data, filename, _ = node.retrieve_file()
    if file_data:
        file_stream = BytesIO(file_data)
        file_stream.seek(0)
        return send_file(file_stream, download_name=filename, as_attachment=True)
    flash('File not found')
    return redirect(url_for('index'))

@app.route('/leave', methods=['POST'])
def leave():
    node.leave_network()
    flash('Left the network and deleted data')
    return redirect(url_for('index'))

if __name__ == "__main__":
    app.run(debug=True, port=3050)
