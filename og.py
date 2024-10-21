import socket
import threading
import pickle
from cryptography.fernet import Fernet
import os

# Generate encryption key for each user (in reality, you would save/load this securely)
key = Fernet.generate_key()
cipher = Fernet(key)

# Node class (a peer in the network)
class Node:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.peers = []
        self.storage = {}
        self.server_thread = threading.Thread(target=self.start_server)
        self.server_thread.start()

    def start_server(self):
        # Peer server that listens for connections
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind((self.host, self.port))
        server.listen(5)
        print(f"Node started on {self.host}:{self.port}, listening for peers...")
        while True:
            client, address = server.accept()
            print(f"Connected to peer: {address}")
            threading.Thread(target=self.handle_peer, args=(client,)).start()

    def handle_peer(self, client):
        # Handling communication with peers
        try:
            while True:
                data = client.recv(1024)
                if data:
                    command, content = pickle.loads(data)
                    if command == 'STORE':
                        peer_id, encrypted_data = content
                        self.storage[peer_id] = encrypted_data
                        print(f"Data stored for peer {peer_id}")
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
        # Connect to an existing peer and share data
        peer = (peer_host, peer_port)
        self.peers.append(peer)
        print(f"Joined network at {peer_host}:{peer_port}")

    def store_data(self, peer_host, peer_port, data):
        # Encrypt the data and send to a peer for storage
        encrypted_data = cipher.encrypt(data.encode('utf-8'))
        peer = (peer_host, peer_port)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.connect(peer)
            client.send(pickle.dumps(('STORE', (self.port, encrypted_data))))
        print(f"Data stored in network at {peer_host}:{peer_port}")

    def retrieve_data(self, peer_host, peer_port):
        # Retrieve encrypted data from peer and decrypt it
        peer = (peer_host, peer_port)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.connect(peer)
            client.send(pickle.dumps(('RETRIEVE', self.port)))
            response = pickle.loads(client.recv(1024))
            if response[0] == 'DATA':
                encrypted_data = response[1]
                decrypted_data = cipher.decrypt(encrypted_data).decode('utf-8')
                print(f"Retrieved and decrypted data: {decrypted_data}")
            else:
                print("Error retrieving data")

    def leave_network(self):
        # When leaving, delete data and notify peers (placeholder logic)
        self.storage.clear()
        print(f"Data deleted, leaving the network.")

# Main program for testing
def main():
    # Create a new peer (node)
    node1 = Node(host='localhost', port=5000)
    
    # Create another peer (to simulate a network)
    node2 = Node(host='localhost', port=5001)
    
    # Join node2 to node1's network
    node2.join_network('localhost', 5000)

    # Node1 stores data on node2
    node1.store_data('localhost', 5001, 'Sensitive data from Node1')

    # Node1 retrieves its data from node2
    node1.retrieve_data('localhost', 5001)

    # Node1 leaves the network
    node1.leave_network()

if __name__ == "__main__":
    main()
