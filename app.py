from flask import Flask, render_template, request, redirect, url_for, flash
import threading
import socket
import pickle
from cryptography.fernet import Fernet

app = Flask(__name__)
app.secret_key = 'your_secret_key'

# Peer Node class (same as before, minor changes for web integration)
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
        server.bind((self.host, self.port))
        server.listen(5)
        while True:
            client, address = server.accept()
            threading.Thread(target=self.handle_peer, args=(client,)).start()

    def handle_peer(self, client):
        try:
            while True:
                data = client.recv(1024)
                if data:
                    command, content = pickle.loads(data)
                    if command == 'STORE':
                        peer_id, encrypted_data = content
                        self.storage[peer_id] = encrypted_data
                    elif command == 'RETRIEVE':
                        peer_id = content
                        if peer_id in self.storage:
                            encrypted_data = self.storage[peer_id]
                            client.send(pickle.dumps(('DATA', encrypted_data)))
                        else:
                            client.send(pickle.dumps(('ERROR', 'Data not found')))
        finally:
            client.close()

    def join_network(self, peer_host, peer_port):
        self.peers.append((peer_host, peer_port))

    def store_data(self, peer_host, peer_port, data):
        encrypted_data = self.cipher.encrypt(data.encode('utf-8'))
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.connect((peer_host, peer_port))
            client.send(pickle.dumps(('STORE', (self.port, encrypted_data))))

    def retrieve_data(self, peer_host, peer_port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.connect((peer_host, peer_port))
            client.send(pickle.dumps(('RETRIEVE', self.port)))
            response = pickle.loads(client.recv(1024))
            if response[0] == 'DATA':
                return self.cipher.decrypt(response[1]).decode('utf-8')
            return None

    def leave_network(self):
        self.storage.clear()

# Initialize the node
node = Node(host='localhost', port=5001)

# Flask routes
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
    data = request.form['data']
    node.store_data(peer_host, peer_port, data)
    flash('Data stored successfully')
    return redirect(url_for('index'))

@app.route('/retrieve', methods=['POST'])
def retrieve():
    peer_host = request.form['peer_host']
    peer_port = int(request.form['peer_port'])
    data = node.retrieve_data(peer_host, peer_port)
    if data:
        flash(f'Data retrieved: {data}')
    else:
        flash('Failed to retrieve data')
    return redirect(url_for('index'))

@app.route('/leave', methods=['POST'])
def leave():
    node.leave_network()
    flash('Left the network and deleted data')
    return redirect(url_for('index'))

if __name__ == "__main__":
    app.run(debug=True, port=5000)
    