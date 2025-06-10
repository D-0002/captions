#!/usr/bin/env python3
"""
Auto Caption Web App
Flask-based web interface for video captioning
"""

from flask import Flask, render_template, request, jsonify, send_file, url_for
import requests
import json
import time
import subprocess
import os
import tempfile
import uuid
from pathlib import Path
from werkzeug.utils import secure_filename
import threading
from datetime import datetime, timedelta

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB max file size
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['OUTPUT_FOLDER'] = 'outputs'

# Create directories if they don't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)

# Your AssemblyAI API key
ASSEMBLYAI_API_KEY = "55b99db8c9804e31bb1d978f81766379"

# Store job status
jobs = {}

class AutoCaptionGenerator:
    def __init__(self, api_key):
        self.api_key = api_key
        self.base_url = "https://api.assemblyai.com"
        self.headers = {"authorization": api_key}
    
    def extract_audio(self, video_path, audio_path):
        """Extract audio from video using FFmpeg"""
        print(f"Extracting audio from: {video_path}")
        print(f"Output audio path: {audio_path}")
        
        # Use WAV for higher compatibility and reliability with the transcription service
        cmd = [
            'ffmpeg', '-i', video_path,
            '-vn', '-acodec', 'pcm_s16le',
            '-ar', '44100', '-ac', '2',
            '-y', audio_path
        ]
        
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            print("Audio extraction successful")
            
            # Check if output file exists and has content
            if os.path.exists(audio_path) and os.path.getsize(audio_path) > 0:
                print(f"Audio file created successfully: {os.path.getsize(audio_path)} bytes")
                return True
            else:
                print("Audio file was not created or is empty")
                return False
                
        except subprocess.CalledProcessError as e:
            print(f"FFmpeg error: {e}")
            print(f"FFmpeg stderr: {e.stderr}")
            return False
        except Exception as e:
            print(f"Unexpected error during audio extraction: {e}")
            return False
    
    def upload_audio(self, audio_path):
        """Upload audio file to AssemblyAI"""
        print(f"Uploading audio file: {audio_path}")
        print(f"File size: {os.path.getsize(audio_path)} bytes")
        
        try:
            with open(audio_path, "rb") as f:
                response = requests.post(
                    f"{self.base_url}/v2/upload",
                    headers=self.headers,
                    data=f,
                    timeout=60  # 60 second timeout for upload
                )
            
            print(f"Upload response status: {response.status_code}")
            
            if response.status_code == 200:
                upload_url = response.json()["upload_url"]
                print(f"Upload successful. URL: {upload_url}")
                return upload_url
            else:
                print(f"Upload failed: {response.status_code} - {response.text}")
                return None
                
        except requests.exceptions.RequestException as e:
            print(f"Upload request error: {e}")
            return None
        except Exception as e:
            print(f"Unexpected upload error: {e}")
            return None
    
    def transcribe_audio(self, audio_url, job_id):
        """Transcribe audio with word-level timestamps"""
        print(f"Starting transcription for job {job_id}")
        print(f"Audio URL: {audio_url}")
        
        # 'word_timestamps' is deprecated; word details are included by default.
        data = {
            "audio_url": audio_url,
            "speech_model": "universal",
        }
        
        try:
            response = requests.post(
                f"{self.base_url}/v2/transcript",
                json=data,
                headers=self.headers,
                timeout=30
            )
            
            print(f"Transcription request status: {response.status_code}")
            
            if response.status_code != 200:
                jobs[job_id]['status'] = 'error'
                jobs[job_id]['error'] = f"API Error: {response.status_code} - {response.text}"
                return None
            
            transcript_id = response.json()['id']
            print(f"Transcript ID: {transcript_id}")
            polling_endpoint = f"{self.base_url}/v2/transcript/{transcript_id}"
            
            # Poll for completion with timeout
            max_polls = 120  # Maximum 6 minutes (120 * 3 seconds)
            poll_count = 0
            
            while poll_count < max_polls:
                try:
                    result = requests.get(polling_endpoint, headers=self.headers, timeout=30).json()
                    print(f"Poll {poll_count}: Status = {result['status']}")
                    
                    if result['status'] == 'completed':
                        print("Transcription completed successfully")
                        return result
                    elif result['status'] == 'error':
                        error_msg = result.get('error', 'Unknown transcription error')
                        print(f"Transcription error: {error_msg}")
                        jobs[job_id]['status'] = 'error'
                        jobs[job_id]['error'] = f"Transcription failed: {error_msg}"
                        return None
                    else:
                        jobs[job_id]['status'] = f"transcribing ({result['status']})"
                        time.sleep(3)
                        poll_count += 1
                        
                except requests.exceptions.RequestException as e:
                    print(f"Request error during polling: {e}")
                    jobs[job_id]['status'] = 'error'
                    jobs[job_id]['error'] = f"Network error during transcription: {str(e)}"
                    return None
            
            # Timeout reached
            jobs[job_id]['status'] = 'error'
            jobs[job_id]['error'] = 'Transcription timeout - please try with a shorter video'
            return None
            
        except requests.exceptions.RequestException as e:
            print(f"Request error: {e}")
            jobs[job_id]['status'] = 'error'
            jobs[job_id]['error'] = f"Network error: {str(e)}"
            return None
        except Exception as e:
            print(f"Unexpected error: {e}")
            jobs[job_id]['status'] = 'error'
            jobs[job_id]['error'] = f"Unexpected error: {str(e)}"
            return None
    
    def create_caption_filters(self, words):
        """Create FFmpeg filter for word-by-word captions"""
        if not words:
            return ""
        
        filters = []
        
        for word in words:
            start_time = word['start'] / 1000.0
            end_time = word['end'] / 1000.0
            text = word['text'].upper()

            # Remove unwanted punctuation (commas, periods) and quotes for clean display
            text = text.replace(',', '').replace('.', '').replace("'", "").replace('"', '')

            # Escape special characters for FFmpeg filtergraph
            text = text.replace(':', '\\:')
            
            # Caption style: centered on screen
            filter_str = (
                f"drawtext=text='{text}'"
                f":fontsize=h/25"
                f":fontcolor=white"
                f":x=(w-text_w)/2"
                f":y=(h-text_h)/2"  # Vertically and horizontally centered
                f":enable='between(t,{start_time},{end_time})'"
                f":box=1:boxcolor=black@0.5:boxborderw=5"  # Semi-transparent background box
            )
            filters.append(filter_str)
        
        return ",".join(filters)
    
    def generate_captioned_video(self, input_video, output_video, words, job_id):
        """Generate video with captions using FFmpeg"""
        if not words:
            jobs[job_id]['status'] = 'error'
            jobs[job_id]['error'] = 'No words were transcribed to generate captions.'
            return False
        
        caption_filters = self.create_caption_filters(words)
        if not caption_filters:
            jobs[job_id]['status'] = 'error'
            jobs[job_id]['error'] = 'Could not create caption filters.'
            return False

        cmd = [
            'ffmpeg',
            '-i', input_video,
            '-vf', caption_filters,
            '-c:a', 'copy',
            '-c:v', 'libx264',
            '-preset', 'fast',
            '-y',
            output_video
        ]
        
        try:
            jobs[job_id]['status'] = 'rendering video'
            print("Running FFmpeg to generate captioned video...")
            # Set a timeout for the rendering process
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600) # 10 minute timeout
            print("FFmpeg rendering completed.")
            return True
        except subprocess.CalledProcessError as e:
            print(f"FFmpeg rendering error: {e}")
            print(f"FFmpeg stderr: {e.stderr}")
            jobs[job_id]['status'] = 'error'
            jobs[job_id]['error'] = 'Failed to render captioned video. Check FFmpeg logs.'
            return False
        except subprocess.TimeoutExpired:
            print("FFmpeg rendering timed out.")
            jobs[job_id]['status'] = 'error'
            jobs[job_id]['error'] = 'Video rendering took too long. Please try a shorter video.'
            return False

    def process_video(self, input_path, output_path, job_id):
        """Main function to process video and add captions"""
        temp_audio_path = None
        
        try:
            jobs[job_id]['status'] = 'extracting audio'
            
            # Create temporary audio file with .wav extension
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_audio_file:
                temp_audio_path = temp_audio_file.name
            
            # Extract audio
            if not self.extract_audio(input_path, temp_audio_path):
                jobs[job_id]['status'] = 'error'
                jobs[job_id]['error'] = 'Failed to extract audio'
                return False
            
            jobs[job_id]['status'] = 'uploading audio'
            
            # Upload audio
            audio_url = self.upload_audio(temp_audio_path)
            if not audio_url:
                jobs[job_id]['status'] = 'error'
                jobs[job_id]['error'] = 'Failed to upload audio'
                return False
            
            jobs[job_id]['status'] = 'transcribing'
            
            # Transcribe
            transcript_result = self.transcribe_audio(audio_url, job_id)
            if not transcript_result or 'words' not in transcript_result:
                # Error is set inside transcribe_audio
                if not jobs[job_id].get('error'):
                    jobs[job_id]['status'] = 'error'
                    jobs[job_id]['error'] = 'Failed to transcribe audio'
                return False
            
            # Generate captioned video
            success = self.generate_captioned_video(
                input_path, output_path, transcript_result['words'], job_id
            )
            
            if success:
                jobs[job_id]['status'] = 'completed'
                jobs[job_id]['output_file'] = os.path.basename(output_path)
                return True
            else:
                # Error is set inside generate_captioned_video
                if not jobs[job_id].get('error'):
                    jobs[job_id]['status'] = 'error'
                    jobs[job_id]['error'] = 'Failed to generate captioned video'
                return False
            
        finally:
            # Clean up temporary audio file
            if temp_audio_path and os.path.exists(temp_audio_path):
                os.unlink(temp_audio_path)

def process_video_background(input_path, output_path, job_id):
    """Background task to process video"""
    generator = AutoCaptionGenerator(ASSEMBLYAI_API_KEY)
    generator.process_video(input_path, output_path, job_id)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_video():
    if 'video' not in request.files:
        return jsonify({'error': 'No video file provided'}), 400
    
    file = request.files['video']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if not file.filename.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
        return jsonify({'error': 'Please upload a valid video file'}), 400
    
    # Generate unique job ID
    job_id = str(uuid.uuid4())
    
    # Save uploaded file
    filename = secure_filename(file.filename)
    input_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{job_id}_{filename}")
    file.save(input_path)
    
    # Generate output path
    output_filename = f"{job_id}_captioned.mp4"
    output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)
    
    # Initialize job status
    jobs[job_id] = {
        'status': 'queued',
        'created_at': datetime.now(),
        'input_file': filename,
        'output_file': None,
        'error': None
    }
    
    # Start background processing
    thread = threading.Thread(
        target=process_video_background,
        args=(input_path, output_path, job_id)
    )
    thread.daemon = True
    thread.start()
    
    return jsonify({'job_id': job_id, 'status': 'processing started'})

@app.route('/status/<job_id>')
def get_status(job_id):
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    job = jobs[job_id]
    return jsonify({
        'status': job['status'],
        'input_file': job['input_file'],
        'output_file': job['output_file'],
        'error': job['error']
    })

@app.route('/download/<job_id>')
def download_video(job_id):
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    job = jobs[job_id]
    if job['status'] != 'completed' or not job['output_file']:
        return jsonify({'error': 'Video not ready'}), 400
    
    output_path = os.path.join(app.config['OUTPUT_FOLDER'], job['output_file'])
    if not os.path.exists(output_path):
        return jsonify({'error': 'Output file not found'}), 404
    
    return send_file(output_path, as_attachment=True, download_name=f"captioned_{job['input_file']}")

# Clean up old files periodically
def cleanup_old_files():
    """Remove files and job entries older than 1 hour"""
    while True:
        try:
            cutoff_time = datetime.now() - timedelta(hours=1)
            
            # Create a copy of job IDs to iterate over, as we'll be modifying the dict
            job_ids_to_check = list(jobs.keys())

            for job_id in job_ids_to_check:
                job = jobs.get(job_id)
                if job and job['created_at'] < cutoff_time:
                    print(f"Cleaning up old job: {job_id}")
                    # Remove files
                    for folder in [app.config['UPLOAD_FOLDER'], app.config['OUTPUT_FOLDER']]:
                        # Use a pattern that is safe for filenames
                        pattern = f"{job_id}_*"
                        for file_path in Path(folder).glob(pattern):
                            try:
                                os.remove(file_path)
                                print(f"Removed old file: {file_path}")
                            except OSError as e:
                                print(f"Error removing file {file_path}: {e}")
                    
                    # Remove job from memory
                    del jobs[job_id]

        except Exception as e:
            print(f"Error during cleanup: {e}")

        # Sleep for an hour before running again
        time.sleep(3600)


if __name__ == '__main__':
    # Start cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_old_files, daemon=True)
    cleanup_thread.start()
    
    app.run(debug=True, host='0.0.0.0', port=5000)