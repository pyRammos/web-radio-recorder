# Web Radio Recorder

A web-based application to schedule and record internet radio streams, manage recordings, and generate podcast feeds.

![Web Radio Recorder](https://raw.githubusercontent.com/pyRammos/web-radio-recorder/main/static/screenshot.png)

## Features

- 🎙️ Record internet radio streams on schedule
- 📅 Support for one-time and recurring recordings (daily, weekly, weekdays, weekends, monthly)
- 🎧 Stream and download recorded audio files directly from the web interface
- 📲 Generate podcast RSS feeds from recordings for use with podcast apps
- 👤 User authentication with secure password management
- 🔐 Force password change for default credentials
- 📱 Responsive design for mobile and desktop use

## Project Structure

```
web-radio-recorder
├── app
│   ├── __init__.py
│   ├── config.py
│   ├── models.py
│   ├── routes.py
│   ├── static
│   │   ├── css
│   │   │   └── style.css
│   │   └── js
│   │       └── player.js
│   └── templates
│       ├── base.html
│       ├── index.html
│       └── recordings.html
├── recordings
│   └── .gitkeep
├── app.py
├── requirements.txt
└── README.md
```

## Installation

### Prerequisites

- Python 3.9+
- FFmpeg (for audio recording)
- Git (for cloning the repository)
- Docker and Docker Compose (for containerized deployment)

### Step 1: Clone the repository

```bash
git clone https://github.com/pyRammos/web-radio-recorder.git
cd web-radio-recorder
```

### Step 2: Create a virtual environment

```bash
python -m venv venv
```

### Step 3: Activate the virtual environment

- On Windows:
  ```bash
  venv\Scripts\activate
  ```
- On macOS/Linux:
  ```bash
  source venv/bin/activate
  ```

### Step 4: Install the required packages

```bash
pip install -r requirements.txt
```

## Usage

### Default Login Credentials

When you first install and run the application, you can log in with the following default credentials:

- Username: `admin`
- Password: `password123`

**Important**: For security reasons, you will be prompted to change the default password upon your first login.

### Run as a standalone script

1. Run the application:
   ```bash
   python app.py
   ```

2. Open your web browser and go to `http://127.0.0.1:5000` to access the application.

### Run as a systemd service

1. Create a systemd service file:
   ```bash
   sudo nano /etc/systemd/system/web-radio-recorder.service
   ```

2. Add the following content to the file:
   ```
   [Unit]
   Description=Web Radio Recorder
   After=network.target

   [Service]
   User=your-username
   WorkingDirectory=/home/your-username/projects/web-radio-recorder
   ExecStart=/home/your-username/projects/web-radio-recorder/venv/bin/python app.py
   Restart=always

   [Install]
   WantedBy=multi-user.target
   ```

   Replace `your-username` with your actual username.

3. Reload systemd to recognize the new service:
   ```bash
   sudo systemctl daemon-reload
   ```

4. Start the service:
   ```bash
   sudo systemctl start web-radio-recorder
   ```

5. Enable the service to start on boot:
   ```bash
   sudo systemctl enable web-radio-recorder
   ```

6. Check the status of the service:
   ```bash
   sudo systemctl status web-radio-recorder
   ```

### Run with Docker

1. Pull the Docker image from DockerHub:
   ```bash
   docker pull teleram/web-radio-recorder
   ```
   
   Alternatively, build the Docker image locally:
   ```bash
   docker build -t web-radio-recorder .
   ```

2. Run the container:
   ```bash
   # If using the DockerHub image:
   docker run -d -p 5000:5000 --name web-radio-recorder teleram/web-radio-recorder
   
   # If using the locally built image:
   docker run -d -p 5000:5000 --name web-radio-recorder web-radio-recorder
   ```

3. Open your web browser and go to `http://127.0.0.1:5000` to access the application.

### Run with Docker Compose

1. Create a `docker-compose.yml` file in the project directory with the following content:
   ```yaml
   version: '3.8'

   services:
     web-radio-recorder:
       image: teleram/web-radio-recorder
       # Alternatively, build locally:
       # build: .
       ports:
         - "5000:5000"
       volumes:
         - ./recordings:/app/recordings
       restart: always
   ```

2. Start the application using Docker Compose:
   ```bash
   docker-compose up -d
   ```

3. Open your web browser and go to `http://127.0.0.1:5000` to access the application.

4. To stop the application:
   ```bash
   docker-compose down
   ```

## Contributing

Contributions are welcome! Please open an issue or submit a pull request for any enhancements or bug fixes.

## License

This project is licensed under the MIT License. See the LICENSE file for more details.