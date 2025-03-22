# At the beginning of your file
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Add the missing imports
from flask import Flask, render_template, request, redirect, url_for, send_file, Response
from datetime import datetime, timedelta
import uuid
import subprocess
import logging
from logging.handlers import RotatingFileHandler

# Continue with existing imports
import json
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.memory import MemoryJobStore
import shutil
import time
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import zipfile
from io import BytesIO
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import sqlalchemy
import flask_sqlalchemy
from functools import wraps
from flask import redirect, url_for, flash

# Fix SQLAlchemy compatibility issues if needed
if not hasattr(sqlalchemy, '__all__'):  
    sqlalchemy.__all__ = []

# Get configurable paths from environment
RECORDINGS_FOLDER = os.environ.get('RECORDINGS_FOLDER', os.path.join(os.path.dirname(__file__), 'recordings'))
LOGS_PATH = os.environ.get('LOGS_PATH', os.path.join(os.path.dirname(__file__), 'logs'))
INSTANCE_PATH = os.environ.get('INSTANCE_PATH', None)  # Flask will use default if None

# Set instance path if provided
if INSTANCE_PATH:
    app = Flask(__name__, instance_path=INSTANCE_PATH)
else:
    app = Flask(__name__)

# Create the Flask application
app.secret_key = os.environ.get('SECRET_KEY')
if not app.secret_key:
    raise RuntimeError('SECRET_KEY environment variable is not set. Refusing to start in production without a secure key.')
app.config['RECORDINGS_FOLDER'] = RECORDINGS_FOLDER
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///radio_recorder.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

if not os.environ.get('FLASK_DEBUG', 'False').lower() == 'true':
    # Set up logging for production
    log_dir = LOGS_PATH
    os.makedirs(log_dir, exist_ok=True)
    
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, 'app.log'),
        maxBytes=1024 * 1024 * 5,  # 5 MB
        backupCount=5
    )
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
    ))
    file_handler.setLevel(logging.INFO)
    
    app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)
    app.logger.info('Web Radio Recorder startup')

# Initialize database
db = SQLAlchemy(app)

# Update your scheduler to use SQLAlchemy for job storage
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

# Initialize the scheduler with persistent storage
jobstores = {
    'default': SQLAlchemyJobStore(url=app.config['SQLALCHEMY_DATABASE_URI'])
}
scheduler = BackgroundScheduler(jobstores=jobstores)

# Define database models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    password_change_required = db.Column(db.Boolean, default=False)  # Add this line
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
        
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class UserSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    nextcloud_url = db.Column(db.String(255))
    nextcloud_username = db.Column(db.String(100))
    nextcloud_password = db.Column(db.String(255))
    
    # Relationship with User model
    user = db.relationship('User', backref=db.backref('settings', uselist=False))

class Station(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    url = db.Column(db.String(255), nullable=False)
    recordings = db.relationship('Recording', backref='station', lazy=True)

class Recording(db.Model):
    id = db.Column(db.String(36), primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey('station.id'), nullable=False)
    start_time = db.Column(db.DateTime)
    duration_minutes = db.Column(db.Integer, nullable=False)
    duration_seconds = db.Column(db.Integer, nullable=False)
    output_file = db.Column(db.String(255))
    status = db.Column(db.String(20), default='scheduled')
    created_at = db.Column(db.DateTime, default=datetime.now)
    recurring = db.Column(db.String(50))
    recurring_type = db.Column(db.String(20))
    actual_start_time = db.Column(db.DateTime)
    error = db.Column(db.Text)
    file_size = db.Column(db.Integer)
    
    # Podcast related fields
    is_podcast = db.Column(db.Boolean, default=False)
    podcast_uuid = db.Column(db.String(36))
    podcast_title = db.Column(db.String(100))
    podcast_description = db.Column(db.Text)
    podcast_language = db.Column(db.String(10))
    podcast_author = db.Column(db.String(100))
    podcast_email = db.Column(db.String(100))
    podcast_category = db.Column(db.String(50))
    podcast_explicit = db.Column(db.String(5))
    podcast_image = db.Column(db.String(255))
    
    # Nextcloud integration
    save_to_nextcloud = db.Column(db.Boolean, default=False)
    nextcloud_folder = db.Column(db.String(255))
    nextcloud_status = db.Column(db.String(20))  # success, failed, pending
    nextcloud_error = db.Column(db.Text)

    # Retention settings
    max_recordings = db.Column(db.Integer, default=0)  # 0 means keep all recordings

    def to_dict(self):
        """Convert record to dictionary for scheduler compatibility"""
        result = {
            'id': self.id,
            'station_id': self.station_id,
            'station_name': self.station.name if self.station else None,
            'station_url': self.station.url if self.station else None,
            'start_time': self.start_time,
            'duration_minutes': self.duration_minutes,
            'duration_seconds': self.duration_seconds,
            'output_file': self.output_file,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'actual_start_time': self.actual_start_time.isoformat() if self.actual_start_time else None,
            'file_size': self.file_size
        }
        
        if self.recurring:
            result['recurring'] = self.recurring
            result['recurring_type'] = self.recurring_type
            
        if self.is_podcast:
            result['podcast'] = {
                'uuid': self.podcast_uuid,
                'title': self.podcast_title,
                'description': self.podcast_description,
                'language': self.podcast_language,
                'author': self.podcast_author,
                'email': self.podcast_email,
                'category': self.podcast_category,
                'explicit': self.podcast_explicit,
                'image': self.podcast_image
            }
            
        return result

# Initialize login manager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def password_change_required(func):
    @wraps(func)
    def decorated_view(*args, **kwargs):
        if current_user.is_authenticated and current_user.password_change_required:
            flash('You must change your default password before continuing.', 'warning')
            return redirect(url_for('change_password', next=request.path))
        return func(*args, **kwargs)
    return decorated_view

def record_audio(recording_id, station_url, output_file, duration_seconds):
    """Record audio from a stream URL to a file for a specified duration with retry capability."""
    with app.app_context():  # Create app context for database access
        # Find the recording
        recording_db = Recording.query.get(recording_id)
        if not recording_db:
            print(f"Recording {recording_id} not found.")
            return
        
        # Create a unique output file based on current date/time with consistent format
        # Get base directory and file information
        base_directory = os.path.dirname(output_file)
        extension = os.path.splitext(output_file)[1]
        
        # Format: [StationName][YYYYMMDD-DDD].mp3
        current_time = datetime.now()
        formatted_date = current_time.strftime('%Y%m%d')
        day_name = current_time.strftime('%a')  # 3-letter day abbreviation (Sun, Mon, etc.)
        
        # Use station name, date and day of week for the new format
        episode_title = f"{recording_db.station.name}{formatted_date}-{day_name}"
        new_output_file = os.path.join(base_directory, f"{episode_title}{extension}")
        
        # Use the new filename
        output_file = new_output_file
        
        # Create the output directory if it doesn't exist
        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
        
        # Update the recording status to 'in_progress'
        recording_db.status = 'in_progress'
        recording_db.actual_start_time = datetime.now()
        recording_db.output_file = output_file  # Save the actual output file path
        db.session.commit()
        
        # Log start of recording
        app.logger.info(f"Starting recording: {recording_id}")
        app.logger.info(f"  - Output file: {output_file}")
        app.logger.info(f"  - Duration: {duration_seconds} seconds")
        app.logger.info(f"  - URL: {station_url}")
        
        # Create the ffmpeg command to record the stream
        max_retries = 3
        retry_count = 0
        success = False
        
        while retry_count < max_retries and not success:
            try:
                # Construct ffmpeg command
                command = [
                    'ffmpeg',
                    '-y',  # Overwrite output file if it exists
                    '-i', station_url,
                    '-t', str(duration_seconds),
                    '-c:a', 'copy',  # Copy audio stream without re-encoding if possible
                    '-v', 'warning',  # Only show warnings and errors
                    output_file
                ]
                
                # Start the process and capture output
                app.logger.info(f"Running command: {' '.join(command)}")
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                
                # Wait for the process to complete
                stdout, stderr = process.communicate()
                
                # Check if the recording was successful
                if process.returncode == 0:
                    # Success - update recording status
                    recording_db.status = 'completed'
                    recording_db.end_time = datetime.now()
                    db.session.commit()
                    
                    app.logger.info(f"Recording completed successfully: {recording_id}")
                    
                    # If this is a recurring job with Nextcloud settings, try to upload
                    if recording_db.recurring and recording_db.nextcloud_enabled:
                        app.logger.info(f"Attempting Nextcloud upload for recording: {recording_id}")
                        
                        # Get the user for their Nextcloud settings
                        creator = User.query.get(recording_db.user_id) if recording_db.user_id else None
                        
                        if creator and creator.nextcloud_url and creator.nextcloud_username and creator.nextcloud_password:
                            # Determine remote path
                            remote_path = recording_db.nextcloud_folder or '/Recordings/'
                            if not remote_path.endswith('/'):
                                remote_path += '/'
                            remote_path += os.path.basename(output_file)
                            
                            # Attempt upload
                            success, message = upload_to_nextcloud(
                                output_file,
                                remote_path,
                                creator.nextcloud_url,
                                creator.nextcloud_username,
                                creator.nextcloud_password
                            )
                            
                            if success:
                                app.logger.info(f"Nextcloud upload successful: {message}")
                                recording_db.nextcloud_status = 'Uploaded'
                                recording_db.nextcloud_path = remote_path
                            else:
                                app.logger.error(f"Nextcloud upload failed: {message}")
                                recording_db.nextcloud_status = f'Failed: {message}'
                            
                            db.session.commit()
                    
                    # Run retention policy check if needed
                    if recording_db.max_recordings > 0 and recording_db.podcast_uuid:
                        clean_up_old_recordings(recording_db.podcast_uuid, recording_db.max_recordings)
                    
                    success = True
                else:
                    # Failure - log errors and retry
                    error_msg = stderr.decode('utf-8', errors='replace')
                    app.logger.error(f"Recording failed: {error_msg}")
                    retry_count += 1
                    
                    if retry_count < max_retries:
                        app.logger.info(f"Retrying recording ({retry_count}/{max_retries})...")
                        time.sleep(5)  # Wait 5 seconds before retrying
                    else:
                        # Max retries reached, update status to failed
                        recording_db.status = 'failed'
                        recording_db.end_time = datetime.now()
                        recording_db.error_message = error_msg
                        db.session.commit()
                        
                        app.logger.error(f"Max retries reached, recording failed: {recording_id}")
            
            except Exception as e:
                # Handle unexpected errors
                app.logger.exception(f"Error recording audio: {str(e)}")
                retry_count += 1
                
                if retry_count >= max_retries:
                    # Max retries reached
                    recording_db.status = 'failed'
                    recording_db.end_time = datetime.now()
                    recording_db.error_message = str(e)
                    db.session.commit()

def schedule_recording_job(recording_id, station_url, output_file, start_time, duration_seconds, recurring=None):
    """Schedule a recording job using the APScheduler."""
    # Check if the job is already scheduled
    job = scheduler.get_job(recording_id)
    if job:
        job.remove()
    
    if recurring:
        # Parse the cron expression
        cron_parts = recurring.split()
        
        # Add the job with a cron trigger
        job = scheduler.add_job(
            record_audio,
            trigger=CronTrigger(
                minute=cron_parts[0],
                hour=cron_parts[1],
                day=cron_parts[2],
                month=cron_parts[3],
                day_of_week=cron_parts[4]
            ),
            id=recording_id,
            args=[recording_id, station_url, output_file, duration_seconds],
            name=f"Recurring Recording {recording_id}"
        )
    else:
        # Add the job with a date trigger (one-time)
        scheduler.add_job(
            record_audio,
            trigger=DateTrigger(run_date=start_time),
            id=recording_id,
            args=[recording_id, station_url, output_file, duration_seconds],
            name=f"Recording {recording_id}"
        )

# Add this function with your other utility functions
def clean_up_old_recordings(podcast_uuid, max_recordings):
    """Delete old recordings based on retention policy"""
    if max_recordings <= 0:
        return  # Keep all recordings
        
    # Get all episodes for this podcast, sorted by date (newest first)
    episodes = Recording.query.filter(
        Recording.podcast_uuid == podcast_uuid,
        Recording.status.in_(['Completed', 'completed']),
        ~Recording.recurring.is_(None)  # Filter out the template
    ).order_by(Recording.actual_start_time.desc()).all()
    
    # If we have more than the max, delete the oldest ones
    if len(episodes) > max_recordings:
        for episode in episodes[max_recordings:]:
            # Delete the file if it exists
            if episode.output_file and os.path.exists(episode.output_file):
                try:
                    os.remove(episode.output_file)
                    app.logger.info(f"Deleted old recording file: {episode.output_file}")
                except OSError as e:
                    app.logger.error(f"Error deleting file {episode.output_file}: {e}")
            
            # Remove from database
            db.session.delete(episode)
            app.logger.info(f"Deleted old recording record: {episode.id}")
            
        db.session.commit()

# Add this as a custom template filter near the top of your app.py file
@app.template_filter('format_datetime')
def format_datetime(value, format='%a %d/%b/%Y at %H:%M'):
    """Format a datetime to a pretty string with day and date."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return value
    
    if isinstance(value, datetime):
        return value.strftime(format)
    return value

# Define all routes below

# ...rest of your routes stay the same...

@app.route('/')
@password_change_required
def index():
    # Get stations from database
    stations = Station.query.all()
    return render_template('index.html', title='Web Radio Recorder', stations=stations)

@app.route('/index.html')
def index_html():
    return index()

@app.route('/add_station', methods=['POST'])
@login_required
def add_station():
    name = request.form.get('name')
    url = request.form.get('url')
    
    if name and url:
        # Add to database
        station = Station(name=name, url=url)
        db.session.add(station)
        db.session.commit()
    
    return redirect(url_for('index'))

@app.route('/delete_station/<int:station_id>', methods=['GET', 'POST'])
@login_required
def delete_station(station_id):
    # Find the station
    station = Station.query.get(station_id)
    
    if station:
        # Delete the station
        db.session.delete(station)
        db.session.commit()
    
    return redirect(url_for('index'))

@app.route('/record/<int:station_id>')
def record_station(station_id):
    # Find the station
    station = Station.query.get(station_id)
    
    if station:
        return render_template('schedule_recording.html', 
                              title=f"Schedule Recording - {station.name}", 
                              station=station)
    
    return "Station not found", 404

@app.route('/schedule_recording', methods=['POST'])
@login_required
def schedule_recording():
    station_id = int(request.form.get('station_id', 0))
    start_date = request.form.get('start_date', '')
    start_time = request.form.get('start_time', '')
    duration = int(request.form.get('duration', 60))  # Duration in minutes
    recurring_type = request.form.get('recurring_type', 'once')
    
    # Find the station
    station = Station.query.get(station_id)
    
    if not station:
        return "Station not found", 404
    
    try:
        # Parse the start date and time
        start_datetime_str = f"{start_date} {start_time}"
        start_datetime = datetime.strptime(start_datetime_str, "%Y-%m-%d %H:%M")
        
        # Create a unique ID and filename for this recording
        recording_id = str(uuid.uuid4())
        filename = f"{station.name}.mp3"  # We'll let record_audio handle the full name formatting
        output_path = os.path.join(app.config['RECORDINGS_FOLDER'], filename)
        
        # Set up cron expression based on recurring type
        recurring = None
        if recurring_type != 'once':
            minute = start_datetime.minute
            hour = start_datetime.hour
            day = '*'
            month = '*'
            day_of_week = '*'
            
            if recurring_type == 'daily':
                # Every day at the specified time
                pass  # Default values are fine
            elif recurring_type == 'weekly':
                # Once a week on the same day at the specified time
                day_of_week = start_datetime.strftime('%w')  # 0-6, Sunday is 0
            elif recurring_type == 'weekdays':
                # Monday through Friday at the specified time
                day_of_week = '1-5'  # Monday-Friday
            elif recurring_type == 'weekends':
                # Saturday and Sunday at the specified time
                day_of_week = '0,6'  # Sunday,Saturday
            elif recurring_type == 'monthly':
                # Same day each month at the specified time
                day = start_datetime.day
            
            recurring = f"{minute} {hour} {day} {month} {day_of_week}"
        
        # Create the recording entry
        recording = Recording(
            id=recording_id,
            station_id=station_id,
            start_time=start_datetime,
            duration_minutes=duration,
            duration_seconds=duration * 60,
            output_file=output_path,
            status='scheduled',
            created_at=datetime.now()
        )
        
        # Add recurring info if specified
        if recurring:
            recording.recurring = recurring
            recording.recurring_type = recurring_type
            
            # Add podcast information for recurring recordings
            podcast_uuid = str(uuid.uuid4())
            recording.is_podcast = True
            recording.podcast_uuid = podcast_uuid
            recording.podcast_title = request.form.get('podcast_title', f"{station.name} Recordings")
            recording.podcast_description = request.form.get('podcast_description', f"Recordings from {station.name}")
            recording.podcast_language = request.form.get('podcast_language', 'en')
            recording.podcast_author = request.form.get('podcast_author', '')
            recording.podcast_email = request.form.get('podcast_email', '')
            recording.podcast_category = request.form.get('podcast_category', 'Music')
            recording.podcast_explicit = request.form.get('podcast_explicit', 'no')
            
            # Handle podcast image upload
            if 'podcast_image' in request.files and request.files['podcast_image'].filename:
                img_file = request.files['podcast_image']
                img_ext = os.path.splitext(img_file.filename)[1]
                img_filename = f"podcast_{podcast_uuid}{img_ext}"
                
                # Create images directory inside the recordings folder
                images_dir = os.path.join(app.config['RECORDINGS_FOLDER'], 'images')
                os.makedirs(images_dir, exist_ok=True)
                
                img_path = os.path.join(images_dir, img_filename)
                img_file.save(img_path)
                
                # Store only the relative path from the recordings folder
                relative_path = os.path.join('images', img_filename)
                recording.podcast_image = relative_path
        
        # Add this in the schedule_recording route, before adding to the database
        # Add retention setting
        if recurring:
            try:
                max_recordings = int(request.form.get('max_recordings', 0))
                recording.max_recordings = max(0, max_recordings)  # Ensure non-negative
            except (ValueError, TypeError):
                recording.max_recordings = 0  # Default to keeping all
        
        # Process Nextcloud options
        save_to_nextcloud = request.form.get('save_to_nextcloud') == 'on'
        recording.save_to_nextcloud = save_to_nextcloud
        if save_to_nextcloud:
            recording.nextcloud_folder = request.form.get('nextcloud_folder', '')
            recording.nextcloud_status = 'pending'
        
        # Add to the schedule
        db.session.add(recording)
        db.session.commit()
        
        # Schedule the recording
        schedule_recording_job(
            recording_id,
            station.url,
            output_path,
            start_datetime,
            duration * 60,
            recurring
        )
        
        return redirect(url_for('recordings'))
        
    except ValueError as e:
        return f"Invalid date or time format: {e}", 400

@app.route('/recordings')
@login_required
@password_change_required
def recordings():
    now = datetime.now()
    
    # Get upcoming recordings from scheduler
    upcoming_recordings = []
    for recording in Recording.query.filter(Recording.status == 'scheduled').all():
        rec_dict = recording.to_dict()
        # Check if it has an active job
        job = scheduler.get_job(recording.id)
        if job and job.next_run_time:
            rec_dict['next_run_time'] = job.next_run_time
        upcoming_recordings.append(rec_dict)
    
    # Sort upcoming recordings by next run time or start time
    upcoming_recordings.sort(key=lambda x: x.get('next_run_time', x.get('start_time', datetime.max)))
    
    # Get past recordings - look for ANY status other than 'scheduled'
    past_recordings = []
    
    # Query with explicit status conditions for better visibility
    past_query = Recording.query.filter(
        Recording.status.in_(['completed', 'Completed', 'in_progress', 'failed', 'Failed'])
    ).all()
    
    app.logger.info(f"Found {len(past_query)} past recordings")
    
    for recording in past_query:
        rec_dict = recording.to_dict()
        # Check if a completed recording is missing its file
        if recording.status in ['Completed', 'completed']:
            if not recording.output_file or not os.path.exists(recording.output_file):
                rec_dict['status'] = 'Missing'
                app.logger.warning(f"Recording {recording.id} file missing: {recording.output_file}")
        past_recordings.append(rec_dict)
    
    # Sort past recordings by start time (newest first)
    past_recordings.sort(key=lambda x: x.get('actual_start_time', x.get('start_time', datetime.min)), reverse=True)
    
    return render_template('recordings.html', 
                          title='Recordings',
                          upcoming_recordings=upcoming_recordings,
                          past_recordings=past_recordings,
                          now=now)

@app.route('/delete_recording/<recording_id>')
@login_required
def delete_recording(recording_id):
    # Find the recording
    recording = Recording.query.get(recording_id)
    
    if recording:
        # Cancel the scheduled job if it exists
        job = scheduler.get_job(recording_id)
        if job:
            job.remove()
        
        # Delete the output file if it exists and not a recurring template
        if os.path.exists(recording.output_file) and recording.status != 'scheduled':
            try:
                os.remove(recording.output_file)
            except OSError as e:
                print(f"Error deleting file: {e}")
        
        # Remove from database
        db.session.delete(recording)
        db.session.commit()
    
    return redirect(url_for('recordings'))

@app.route('/download_recording/<recording_id>')
def download_recording(recording_id):
    # Find the recording in the database
    recording = Recording.query.get(recording_id)
    
    if not recording:
        return "Recording not found", 404
    
    # Check if the file exists
    if not os.path.exists(recording.output_file):
        return "Recording file not found", 404
    
    # Check if the recording is completed
    if recording.status.lower() not in ['completed']:
        return "Recording is not yet available for download", 400
        
    # Get the directory and filename from the output path
    directory = os.path.dirname(recording.output_file)
    filename = os.path.basename(recording.output_file)
    
    # Serve the file as an attachment for download
    return send_file(
        recording.output_file,
        as_attachment=True,
        download_name=filename
    )

@app.route('/recording_file/<recording_id>')
def recording_file(recording_id):
    """Stream a recording file."""
    # Find the recording in the database
    recording = Recording.query.get(recording_id)
    
    if not recording:
        return "Recording not found", 404
    
    # Check if the file exists
    if not os.path.exists(recording.output_file):
        return "Recording file not found", 404
    
    # Send the file for streaming or download
    return send_file(recording.output_file, as_attachment=False)

@app.route('/list_podcasts')
def list_podcasts():
    """List all available podcast feeds from recurring recordings."""
    # Find all recurring recordings with podcast info
    podcasts = {}
    
    # Query recurring recordings with podcast info - use is_podcast flag
    podcast_recordings = Recording.query.filter(
        Recording.is_podcast == True
    ).all()
    
    for recording in podcast_recordings:
        podcast_uuid = recording.podcast_uuid
        
        if podcast_uuid and podcast_uuid not in podcasts:
            podcasts[podcast_uuid] = {
                'uuid': podcast_uuid,
                'title': recording.podcast_title or recording.station.name,
                'description': recording.podcast_description or '',
                'image': recording.podcast_image or '',
                'recording_id': recording.id,
                'station_name': recording.station.name if recording.station else "Unknown"
            }
    
    return render_template('podcasts.html', title='Available Podcasts', podcasts=list(podcasts.values()))

@app.route('/podcast/<podcast_uuid>')
def podcast_feed(podcast_uuid):
    """Generate RSS feed for a specific podcast."""
    # Find the podcast template (recurring recording with matching UUID)
    podcast_template = Recording.query.filter_by(
        podcast_uuid=podcast_uuid,
        is_podcast=True
    ).first()
    
    if not podcast_template:
        return "Podcast not found", 404
        
    # Get completed episodes with this podcast UUID
    episodes = Recording.query.filter(
        Recording.status.in_(['Completed', 'completed']),
        Recording.podcast_uuid == podcast_uuid
    ).all()
    
    # Convert episodes to dictionaries
    episode_dicts = [episode.to_dict() for episode in episodes]
    
    # Sort episodes by date, newest first
    episode_dicts.sort(key=lambda x: x.get('actual_start_time', ''), reverse=True)
    
    # Build the RSS feed
    podcast_info = {
        'uuid': podcast_template.podcast_uuid,
        'title': podcast_template.podcast_title,
        'description': podcast_template.podcast_description,
        'language': podcast_template.podcast_language,
        'author': podcast_template.podcast_author,
        'email': podcast_template.podcast_email,
        'category': podcast_template.podcast_category,
        'explicit': podcast_template.podcast_explicit,
        'image': podcast_template.podcast_image
    }
    
    rss_xml = render_template('podcast_rss.xml',
                             podcast=podcast_info,
                             episodes=episode_dicts,
                             base_url=request.url_root.rstrip('/'))
    
    return Response(rss_xml, mimetype='application/xml')

@app.route('/podcast/image/<podcast_uuid>')
def podcast_image(podcast_uuid):
    """Serve the podcast cover image."""
    # Find the podcast with this UUID
    recording = Recording.query.filter_by(podcast_uuid=podcast_uuid).first()
    
    if recording and recording.podcast_image:
        # Get the image path - could be relative or absolute
        image_path = recording.podcast_image
        
        # If it's a relative path, make it absolute
        if not os.path.isabs(image_path):
            image_path = os.path.join(app.config['RECORDINGS_FOLDER'], image_path)
            
        if os.path.exists(image_path):
            return send_file(image_path)
    
    # Return a default image if not found
    default_image = os.path.join(app.static_folder, 'podcast-default.jpg')
    if os.path.exists(default_image):
        return send_file(default_image)
    
    return "Image not found", 404

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            login_user(user)
            
            # Check if password change is required
            if user.password_change_required:
                flash('Please change your default password.', 'warning')
                return redirect(url_for('change_password', next=request.referrer))
            
            next_page = request.args.get('next')
            if next_page:
                return redirect(next_page)
            return redirect(url_for('index'))
        
        return render_template('login.html', title='Login', error='Invalid username or password')
    
    return render_template('login.html', title='Login')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

# Add a route to change passwords
@app.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        # Validate inputs
        if not current_user.check_password(current_password):
            return render_template('change_password.html', title='Change Password', 
                                  error='Current password is incorrect')
        
        if new_password != confirm_password:
            return render_template('change_password.html', title='Change Password', 
                                  error='New passwords do not match')
        
        if len(new_password) < 8:
            return render_template('change_password.html', title='Change Password', 
                                  error='Password must be at least 8 characters')
        
        # Make sure new password isn't the default
        if new_password == 'password123':
            return render_template('change_password.html', title='Change Password', 
                                  error='You cannot use the default password')
        
        # Update password
        current_user.set_password(new_password)
        current_user.password_change_required = False  # Clear the flag
        db.session.commit()
        
        # Redirect to next page if provided, otherwise show success message
        next_page = request.args.get('next')
        if next_page:
            flash('Password successfully changed.', 'success')
            return redirect(next_page)
        
        return render_template('change_password.html', title='Change Password', 
                              message='Password successfully changed')
    
    # Add 'force' flag to template context if password change is required
    force_change = current_user.password_change_required
    next_url = request.args.get('next')
    
    return render_template('change_password.html', title='Change Password', 
                          force_change=force_change, next=next_url)

@app.route('/health')
def health_check():
    """Health check endpoint for monitoring."""
    try:
        # Test database connectivity
        db.session.execute('SELECT 1').scalar()
        scheduler_status = scheduler.running
        
        status = {
            'status': 'ok',
            'timestamp': datetime.now().isoformat(),
            'scheduler_running': scheduler_status,
            'jobs_count': len(scheduler.get_jobs()),
            'database': 'connected',
            'version': '1.0.0'
        }
        return status, 200
    except Exception as e:
        error_status = {
            'status': 'error',
            'timestamp': datetime.now().isoformat(),
            'error': str(e)
        }
        return error_status, 500

@app.errorhandler(500)
def server_error(e):
    app.logger.error(f"Server error: {str(e)}")
    return render_template('error.html', 
                          error="Internal Server Error", 
                          message="Something went wrong. Please try again or contact the administrator."), 500

@app.errorhandler(404)
def not_found(e):
    return render_template('error.html', 
                          error="Page Not Found", 
                          message="The requested resource could not be found."), 404

@app.route('/admin')
@login_required
def admin_dashboard():
    # Check if user is admin
    if not current_user.is_admin:
        flash('Admin privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get statistics
    stats = {
        'stations_count': Station.query.count(),
        'recordings_count': Recording.query.count(),
        'completed_recordings': Recording.query.filter(Recording.status.in_(['Completed', 'completed'])).count(),
        'failed_recordings': Recording.query.filter(Recording.status == 'Failed').count(),
        'scheduled_jobs': len(scheduler.get_jobs()),
        'users_count': User.query.count(),
    }
    
    return render_template('admin_dashboard.html', title='Admin Dashboard', stats=stats)

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    # Get or create user settings
    user_settings = UserSettings.query.filter_by(user_id=current_user.id).first()
    if not user_settings:
        user_settings = UserSettings(user_id=current_user.id)
        db.session.add(user_settings)
        db.session.commit()
    
    # Handle form submission
    if request.method == 'POST':
        # Password change section
        if 'change_password' in request.form:
            current_password = request.form.get('current_password')
            new_password = request.form.get('new_password')
            confirm_password = request.form.get('confirm_password')
            
            # Validate inputs
            if not current_user.check_password(current_password):
                flash('Current password is incorrect', 'danger')
            elif new_password != confirm_password:
                flash('New passwords do not match', 'danger')
            elif len(new_password) < 8:
                flash('Password must be at least 8 characters', 'danger')
            else:
                # Update password
                current_user.set_password(new_password)
                current_user.password_change_required = False
                db.session.commit()
                flash('Password successfully changed', 'success')
        
        # Nextcloud settings section
        elif 'update_nextcloud' in request.form:
            user_settings.nextcloud_url = request.form.get('nextcloud_url', '').rstrip('/')
            user_settings.nextcloud_username = request.form.get('nextcloud_username', '')
            
            # Only update password if provided (not empty)
            new_password = request.form.get('nextcloud_password', '')
            if new_password:
                user_settings.nextcloud_password = new_password
                
            db.session.commit()
            flash('Nextcloud settings updated', 'success')
            
            # Test connection if requested
            if 'test_connection' in request.form:
                success, message = test_nextcloud_connection(
                    user_settings.nextcloud_url,
                    user_settings.nextcloud_username,
                    user_settings.nextcloud_password
                )
                if success:
                    flash(f'Nextcloud connection successful: {message}', 'success')
                else:
                    flash(f'Nextcloud connection failed: {message}', 'danger')
    
    return render_template('settings.html', title='User Settings', settings=user_settings)

def test_nextcloud_connection(nextcloud_url, username, password):
    """Test connection to Nextcloud server"""
    try:
        import requests
        # Construct the WebDAV URL
        webdav_url = f"{nextcloud_url}/remote.php/dav/files/{username}/"
        
        # Make a request to list root directory
        response = requests.propfind(
            webdav_url,
            auth=(username, password),
            headers={'Depth': '0'}
        )
        
        if response.status_code == 207:  # Multi-status response is good
            return True, "Connection successful"
        else:
            return False, f"Unexpected status code: {response.status_code}"
    
    except Exception as e:
        return False, str(e)

def upload_to_nextcloud(file_path, nextcloud_path, nextcloud_url, username, password):
    """Upload a file to Nextcloud via WebDAV"""
    try:
        import requests
        from pathlib import Path
        
        # Ensure the path starts with a slash
        if not nextcloud_path.startswith('/'):
            nextcloud_path = '/' + nextcloud_path
        
        # Construct the full URL
        webdav_url = f"{nextcloud_url}/remote.php/dav/files/{username}{nextcloud_path}"
        
        # Create directory if it doesn't exist
        dir_path = str(Path(nextcloud_path).parent)
        if dir_path != '/':
            dir_url = f"{nextcloud_url}/remote.php/dav/files/{username}{dir_path}"
            requests.request(
                'MKCOL', 
                dir_url,
                auth=(username, password)
            )
        
        # Upload the file
        with open(file_path, 'rb') as f:
            response = requests.put(
                webdav_url,
                data=f,
                auth=(username, password)
            )
        
        if response.status_code in [200, 201, 204]:
            return True, "File uploaded successfully"
        else:
            return False, f"Upload failed with status code: {response.status_code}"
    
    except Exception as e:
        return False, str(e)

# Initialize the application
def init_app():
    import pytz
    from apscheduler.events import EVENT_ALL
    
    with app.app_context():
        # Create database tables
        db.create_all()
        
        # Add a default admin user if there are no users
        if not User.query.first():
            admin = User(username='admin', is_admin=True, password_change_required=True)
            admin.set_password('password123')
            db.session.add(admin)
            
            # Add some default stations
            default_stations = [
                Station(name="BBC Radio 1", url="https://stream.live.vc.bbcmedia.co.uk/bbc_radio_one"),
                Station(name="Classical Music", url="https://live.musopen.org:8085/streamvbr0"),
                Station(name="Jazz 24", url="https://live.wostreaming.net/direct/ppm-jazz24aac-ibc1")
            ]
            for station in default_stations:
                db.session.add(station)
                
            db.session.commit()
            
            print("Created default admin user and stations")
            print("Username: admin")
            print("Password: password123")
            print("IMPORTANT: Change this password immediately!")
        
        # Update schema with new column (if using SQLite this should work)
        inspector = db.inspect(db.engine)
        if 'password_change_required' not in [c['name'] for c in inspector.get_columns('user')]:
            db.session.execute('ALTER TABLE user ADD COLUMN password_change_required BOOLEAN DEFAULT FALSE')
            db.session.commit()
            
        # Set password_change_required for any user with default password
        for user in User.query.all():
            if check_password_hash(user.password_hash, 'password123'):
                user.password_change_required = True
        db.session.commit()
        
        # Ensure recordings directory exists
        os.makedirs(app.config['RECORDINGS_FOLDER'], exist_ok=True)
        
        # Configure and start scheduler - ONLY ONCE
        if not scheduler.running:
            # Configure scheduler
            scheduler.configure(timezone=pytz.timezone('UTC'))
            scheduler.add_listener(lambda event: app.logger.info(f"Job event: {event.code}"), EVENT_ALL)
            
            # Initialize any pending recordings
            recordings = Recording.query.filter_by(status='scheduled').all()
            now = datetime.now()
            
            for recording in recordings:
                station = Station.query.get(recording.station_id)
                if not station:
                    continue
                    
                if recording.recurring:
                    # Schedule recurring job
                    schedule_recording_job(
                        recording.id,
                        station.url,
                        recording.output_file,
                        recording.start_time,
                        recording.duration_seconds,
                        recording.recurring
                    )
                elif recording.start_time > now:
                    # Schedule one-time job that hasn't passed yet
                    schedule_recording_job(
                        recording.id,
                        station.url,
                        recording.output_file,
                        recording.start_time,
                        recording.duration_seconds
                    )
            
            # Start the scheduler
            scheduler.start()
    
    # Setup graceful shutdown - ONLY ONCE
    def graceful_shutdown():
        """Handle graceful shutdown of scheduler."""
        print("Shutting down scheduler...")
        if scheduler.running:
            scheduler.shutdown()

    # Register the cleanup function
    import atexit
    atexit.register(graceful_shutdown)

@app.route('/debug/scheduler')
def debug_scheduler():
    """Debug info about scheduler"""
    jobs = []
    for job in scheduler.get_jobs():
        trigger_info = {}
        if hasattr(job.trigger, 'run_date'):
            trigger_info['run_date'] = job.trigger.run_date.isoformat() if job.trigger.run_date else None
        elif hasattr(job.trigger, 'fields'):
            trigger_info['fields'] = {f.name: str(f) for f in job.trigger.fields}
        
        jobs.append({
            'id': job.id,
            'name': job.name,
            'next_run_time': job.next_run_time.isoformat() if job.next_run_time else None,
            'trigger': str(job.trigger),
            'trigger_info': trigger_info,
            'func': job.func.__name__,
            'args': str(job.args),
            'kwargs': str(job.kwargs),
            'misfire_grace_time': job.misfire_grace_time,
            'max_instances': job.max_instances
        })
    
    # Get environment info
    import platform
    import pytz
    
    env_info = {
        'os': platform.platform(),
        'python': platform.python_version(),
        'timezone': str(pytz.timezone('UTC')),
        'current_time': datetime.now().isoformat(),
        'utc_time': datetime.utcnow().isoformat()
    }
    
    # Add scheduler state
    scheduler_state = {
        'running': scheduler.running,
        'jobs_count': len(scheduler.get_jobs()),
        'jobstores': list(scheduler._jobstores.keys()),
        'timezone': str(scheduler.timezone) if hasattr(scheduler, 'timezone') else None
    }
    
    # Add Docker info if in Docker container
    docker_info = {'in_container': os.path.exists('/.dockerenv')}
    if docker_info['in_container']:
        try:
            with open('/etc/timezone', 'r') as f:
                docker_info['container_timezone'] = f.read().strip()
        except:
            docker_info['container_timezone'] = 'Could not read /etc/timezone'
    
    response = {
        'scheduler_state': scheduler_state,
        'jobs': jobs,
        'environment': env_info,
        'docker': docker_info
    }
    
    return response

@app.route('/debug/find_orphaned_files')
@login_required
def find_orphaned_files():
    """Find recording files that exist but aren't in the database"""
    recordings_folder = app.config['RECORDINGS_FOLDER']
    
    # Get all recording files
    recording_files = []
    for root, dirs, files in os.walk(recordings_folder):
        for file in files:
            if file.endswith('.mp3'):
                recording_files.append(os.path.join(root, file))
    
    # Get all recordings in the database
    db_recordings = Recording.query.all()
    db_files = [r.output_file for r in db_recordings if r.output_file]
    
    # Find files not in the database
    orphaned_files = []
    for file_path in recording_files:
        if file_path not in db_files:
            # Get file info
            file_size = os.path.getsize(file_path)
            file_mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
            
            orphaned_files.append({
                'path': file_path,
                'filename': os.path.basename(file_path),
                'size': file_size,
                'modified': file_mtime,
                'age_days': (datetime.now() - file_mtime).days
            })
    
    # Sort by modification time (newest first)
    orphaned_files.sort(key=lambda x: x['modified'], reverse=True)
    
    return {
        'orphaned_files_count': len(orphaned_files),
        'orphaned_files': orphaned_files,
        'total_files_count': len(recording_files),
        'database_files_count': len(db_files)
    }

if __name__ == '__main__':
    init_app()
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=debug_mode)