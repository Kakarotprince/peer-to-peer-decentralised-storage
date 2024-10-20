# Peer-to-Peer Encrypted Data Storage Network

This project is a simple **peer-to-peer (P2P) network** where users can join or create a network, store encrypted data, retrieve the data, and leave the network, all via a web interface. The data is encrypted on the user's side before being sent to the network, and it is decrypted upon retrieval. When a user leaves the network, their data is deleted from the storage.

## Features

1. **Join a Network**: A user can either join an existing peer-to-peer network or create a new one.
2. **Store Data**: Users can choose to store data in the network, which will be encrypted before storage.
3. **Retrieve Data**: Users can retrieve and decrypt their data from the network.
4. **Leave Network**: When users leave the network, all their data is deleted.

## Technologies Used

- **Python** for the backend logic
- **Flask** for the web application framework
- **Socket** for peer-to-peer communication
- **Cryptography** (`Fernet` from the `cryptography` package) for encryption and decryption
- **HTML/CSS** for the web interface

## Prerequisites

Make sure you have Python installed (version 3.x). Install the required dependencies by running:

```bash
pip install Flask cryptography
