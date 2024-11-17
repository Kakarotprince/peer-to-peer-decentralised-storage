import os
import threading
import socket
import pickle
import time
from cryptography.fernet import Fernet
from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from werkzeug.utils import secure_filename
from pyngrok import ngrok
from io import BytesIO

app = Flask(__name__)
app.secret_key = 'your_secret_key'

# Constants
UPLOAD_FOLDER = 'uploads'
COMMON_PORT = 5000  # Common port for Flask app
NGROK_PUBLIC_URL = None  # To store ngrok's public URL

# Ensure the upload folder exists
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Node class for managing networked nodes
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
        print(f"Node initialized on {self.host}:{self.port}")

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
                        peer_host, peer_storage_limit = content
                        if peer_host not in [peer[0] for peer in self.peers]:
                            self.peers.append((peer_host, peer_storage_limit))
                            self.broadcast_peers()
                    elif command == 'STORE':
                        peer_id, encrypted_file, filename, timestamp, file_size = content
                        if self.used_storage + file_size <= self.storage_limit:
                            self.storage[peer_id] = (encrypted_file, filename, timestamp)
                            self.used_storage += file_size
                        else:
                            client.send(pickle.dumps(('ERROR', 'Insufficient storage space')))
                    elif command == 'PEERS':
                        self.peers = content
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
                    client.send(pickle.dumps(('PEERS', self.peers)))
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

            if self.used_storage + file_size > self.storage_limit:
                flash("File exceeds available storage space. Cannot store file.")
                return
            
            for peer_host, _ in self.peers:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                    if peer_host != self.host:
                        client.connect((peer_host, COMMON_PORT))
                        client.send(pickle.dumps(('STORE', (self.port, encrypted_file, filename, timestamp, file_size))))
            
            self.used_storage += file_size
            os.remove(filepath)
            flash(f'File {filename} stored successfully across network')
        except Exception as e:
            flash(f'Failed to store file: {str(e)}')

    def retrieve_file(self):
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
    # Add this method inside the Node class
    def calculate_total_network_storage(self):
        """Calculate the total available storage across all peers."""
        # Calculate the remaining storage on the current node
        remaining_storage = self.storage_limit - self.used_storage
        # Convert to MB for consistency
        remaining_storage_mb = remaining_storage / (1024 * 1024)
        
        # Add storage limits of peers
        total_storage_mb = sum(peer[1] for peer in self.peers) + remaining_storage_mb
        return total_storage_mb


# Initialize Node with the current host IP
current_host = socket.gethostbyname(socket.gethostname())
node = None

@app.route('/')
def index():
    global node
    total_storage = node.calculate_total_network_storage() if node else 0
    return render_template('index.html', host_ip=NGROK_PUBLIC_URL or current_host, peers=node.peers if node else [], total_storage=total_storage)

@app.route('/connect', methods=['POST'])
def connect():
    global node
    action = request.form['action']
    storage_limit = int(request.form['storage_limit'])
    node = Node(NGROK_PUBLIC_URL or current_host, storage_limit)

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

    file_data, filename, timestamp = node.retrieve_file()
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
    # Start ngrok
    public_url = ngrok.connect(COMMON_PORT)
    NGROK_PUBLIC_URL = public_url.public_url
    print(f"Ngrok tunnel established: {NGROK_PUBLIC_URL}")

    # Start Flask
    app.run(port=COMMON_PORT)
