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

# Create the Flask application
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY')
if not app.secret_key:
    raise RuntimeError('SECRET_KEY environment variable is not set. Refusing to start in production without a secure key.')
app.config['RECORDINGS_FOLDER'] = os.path.join(os.path.dirname(__file__), 'recordings')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///radio_recorder.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

if not os.environ.get('FLASK_DEBUG', 'False').lower() == 'true':
    # Set up logging for production
    log_dir = os.path.join(os.path.dirname(__file__), 'logs')
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

# Initialize the scheduler - we'll start it later
scheduler = BackgroundScheduler()
scheduler.add_jobstore(MemoryJobStore(), 'default')

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
        
        # For recurring recordings, create a unique output file based on current date/time
        if recording_db.recurring:
            # Get base directory and file information
            base_directory = os.path.dirname(output_file)
            extension = os.path.splitext(output_file)[1]
            
            # Format: StationName-YYYYMMDD
            current_time = datetime.now()
            formatted_date = current_time.strftime('%Y%m%d')
            
            # Use station name and date for the filename
            episode_title = f"{recording_db.station.name}-{formatted_date}"
            new_output_file = os.path.join(base_directory, f"{episode_title}{extension}")
            
            # Use the new filename
            output_file = new_output_file
        
        # Create the output directory if it doesn't exist
        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
        
        # Create the ffmpeg command to record the stream
        cmd = [
            'ffmpeg',
            '-y',  # Overwrite output file if it exists
            '-i', station_url,
            '-t', str(duration_seconds),
            '-c', 'copy',
            output_file
        ]
        
        print(f"Starting recording: {' '.join(cmd)}")
        start_time = time.time()
        
        try:
            # Start recording - don't use text mode to avoid encoding issues
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            
            stdout, stderr = process.communicate()
            
            # Check if the recording completed successfully
            if process.returncode == 0:
                print(f"Recording completed: {output_file}")
                
                # For recurring recordings, create a completed episode
                if recording_db.recurring:
                    # Create a new recording entry for this completed episode
                    episode_id = str(uuid.uuid4())
                    
                    # Make sure file exists before getting its size
                    file_size = 0
                    if os.path.exists(output_file):
                        file_size = os.path.getsize(output_file)
                    
                    episode = Recording(
                        id=episode_id,
                        station_id=recording_db.station_id,
                        start_time=datetime.now(),
                        actual_start_time=datetime.now(),
                        duration_minutes=recording_db.duration_minutes,
                        duration_seconds=recording_db.duration_seconds,
                        output_file=output_file,
                        status='Completed',
                        created_at=datetime.now(),
                        file_size=file_size
                    )
                    
                    # Copy podcast information
                    if recording_db.is_podcast:
                        episode.is_podcast = True
                        episode.podcast_uuid = recording_db.podcast_uuid
                        episode.podcast_title = recording_db.podcast_title
                        episode.podcast_description = recording_db.podcast_description
                        episode.podcast_language = recording_db.podcast_language
                        episode.podcast_author = recording_db.podcast_author
                        episode.podcast_email = recording_db.podcast_email
                        episode.podcast_category = recording_db.podcast_category
                        episode.podcast_explicit = recording_db.podcast_explicit
                        episode.podcast_image = recording_db.podcast_image
                    
                    # Add to the database
                    db.session.add(episode)
                    db.session.commit()
                else:
                    # For one-time recordings, update the existing entry
                    recording_db.status = 'Completed'
                    recording_db.actual_start_time = datetime.now()
                    if os.path.exists(output_file):
                        recording_db.file_size = os.path.getsize(output_file)
                    db.session.commit()
                    
            else:
                print(f"Recording failed with return code {process.returncode}")
                error_msg = "Unknown error"
                
                # Try to decode error message, but handle encoding issues
                if stderr:
                    try:
                        # Try to decode with errors='replace' to handle invalid bytes
                        error_msg = stderr.decode('utf-8', errors='replace')[:200]
                    except Exception as e:
                        error_msg = f"Error decoding message: {str(e)}"
                
                # Update the status of the recording to failed
                if not recording_db.recurring:
                    recording_db.status = 'Failed'
                    recording_db.error = error_msg
                    db.session.commit()
        
        except Exception as e:
            print(f"Error during recording: {e}")
            
            # Update the status of the recording to failed
            if not recording_db.recurring:
                recording_db.status = 'Failed'
                recording_db.error = str(e)
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
        filename = f"{station.name}_{start_datetime.strftime('%Y%m%d_%H%M')}.mp3"
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
@password_change_required
def recordings():
    now = datetime.now()
    
    # Get upcoming recordings from database
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
    
    # Get past recordings from database
    past_recordings = []
    for recording in Recording.query.filter(Recording.status != 'scheduled').all():
        rec_dict = recording.to_dict()
        # Check if a completed recording is missing its file
        if recording.status in ['Completed', 'completed']:
            if not os.path.exists(recording.output_file):
                rec_dict['status'] = 'Missing'
        past_recordings.append(rec_dict)
    
    # Sort past recordings by start time (newest first)
    past_recordings.sort(key=lambda x: x.get('start_time', datetime.min), reverse=True)
    
    return render_template('recordings.html', 
                          title='Recordings',
                          upcoming_recordings=upcoming_recordings,
                          past_recordings=past_recordings)

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
    status = {
        'status': 'ok',
        'timestamp': datetime.now().isoformat(),
        'scheduler_running': scheduler.running,
        'jobs_count': len(scheduler.get_jobs()),
        'version': '1.0.0'
    }
    return status

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

# Initialize the application
def init_app():
    # Make sure database tables exist
    with app.app_context():
        db.create_all()
        
        # Add a default admin user if there are no users
        if not User.query.first():
            admin = User(username='admin', is_admin=True, password_change_required=True)  # Add this flag
            admin.set_password('password123')  # Default password, should be changed
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

    # Ensure recordings directory exists
    os.makedirs(app.config['RECORDINGS_FOLDER'], exist_ok=True)
    
    # Start the scheduler
    if not scheduler.running:
        scheduler.start()

    # In your init_app function, after db.create_all(), add this code to handle existing users
    with app.app_context():
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

    # At the end of your init_app function

    def graceful_shutdown():
        """Handle graceful shutdown of scheduler."""
        print("Shutting down scheduler...")
        scheduler.shutdown()

    # Register the cleanup function
    import atexit
    atexit.register(graceful_shutdown)

if __name__ == '__main__':
    init_app()
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=debug_mode)