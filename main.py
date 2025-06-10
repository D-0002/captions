#!/usr/bin/env python3
"""
Auto Caption Web App
Flask-based web interface for video captioning using AssemblyAI SDK
"""

from flask import Flask, render_template, request, jsonify, send_file, url_for
import assemblyai as aai
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

# Configure AssemblyAI SDK
aai.settings.api_key = "55b99db8c9804e31bb1d978f81766379"

# Store job status
jobs = {}

class AutoCaptionGenerator:
    def __init__(self):
        # Configure transcription settings
        self.config = aai.TranscriptionConfig(
            speech_model=aai.SpeechModel.best
        )
        self.transcriber = aai.Transcriber(config=self.config)
    
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
    
    def transcribe_audio(self, audio_path, job_id):
        """Transcribe audio using AssemblyAI SDK"""
        print(f"Starting transcription for job {job_id}")
        print(f"Audio file: {audio_path}")
        
        try:
            jobs[job_id]['status'] = 'uploading audio'
            
            # The SDK handles file upload automatically
            transcript = self.transcriber.transcribe(audio_path)
            
            # Check if transcription was successful
            if transcript.status == aai.TranscriptStatus.error:
                error_msg = transcript.error or "Unknown transcription error"
                print(f"Transcription error: {error_msg}")
                jobs[job_id]['status'] = 'error'
                jobs[job_id]['error'] = f"Transcription failed: {error_msg}"
                return None
            
            if transcript.status == aai.TranscriptStatus.completed:
                print("Transcription completed successfully")
                return transcript
            else:
                print(f"Unexpected transcript status: {transcript.status}")
                jobs[job_id]['status'] = 'error'
                jobs[job_id]['error'] = f"Unexpected transcript status: {transcript.status}"
                return None
                
        except Exception as e:
            print(f"Transcription error: {e}")
            jobs[job_id]['status'] = 'error'
            jobs[job_id]['error'] = f"Transcription error: {str(e)}"
            return None
    
    def create_caption_filters(self, words):
        """Create FFmpeg filter for word-by-word captions"""
        if not words:
            return ""
        
        filters = []
        
        for word in words:
            start_time = word.start / 1000.0  # SDK provides milliseconds, convert to seconds
            end_time = word.end / 1000.0
            text = word.text.upper()

            # Remove unwanted punctuation (commas, periods) and quotes for clean display
            text = text.replace(',', '').replace('.', '').replace("'", "").replace('"', '')

            # Escape special characters for FFmpeg filtergraph
            text = text.replace(':', '\\:').replace("'", "\\'")
            
            # Caption style: centered on screen with shadow (removed bold option)
            filter_str = (
                f"drawtext=text='{text}'"
                f":fontsize=h/20"  # Made font slightly larger since we can't use bold
                f":fontcolor=white"
                f":x=(w-text_w)/2"
                f":y=(h-text_h)/2"  # Vertically and horizontally centered
                f":enable='between(t,{start_time},{end_time})'"
                f":shadowcolor=black:shadowx=3:shadowy=3"  # Increased shadow for better visibility
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

        # Write filter to temporary file to avoid Windows command line length limits
        filter_file = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
                f.write(caption_filters)
                filter_file = f.name
            
            cmd = [
                'ffmpeg',
                '-i', input_video,
                '-filter_complex_script', filter_file,
                '-c:a', 'copy',
                '-c:v', 'libx264',
                '-preset', 'fast',
                '-y',
                output_video
            ]
            
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
        finally:
            # Clean up filter file
            if filter_file and os.path.exists(filter_file):
                try:
                    os.unlink(filter_file)
                except:
                    pass

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
            
            jobs[job_id]['status'] = 'transcribing'
            
            # Transcribe using SDK
            transcript = self.transcribe_audio(temp_audio_path, job_id)
            if not transcript or not transcript.words:
                # Error is set inside transcribe_audio
                if not jobs[job_id].get('error'):
                    jobs[job_id]['status'] = 'error'
                    jobs[job_id]['error'] = 'Failed to transcribe audio or no words found'
                return False
            
            # Generate captioned video
            success = self.generate_captioned_video(
                input_path, output_path, transcript.words, job_id
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

def process_video_sync(input_path, output_path, job_id):
    """Synchronous video processing"""
    generator = AutoCaptionGenerator()
    return generator.process_video(input_path, output_path, job_id)

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
        'status': 'processing',
        'created_at': datetime.now(),
        'input_file': filename,
        'output_file': None,
        'error': None
    }
    
    # Process video synchronously
    try:
        success = process_video_sync(input_path, output_path, job_id)
        if success:
            return jsonify({'job_id': job_id, 'status': 'completed'})
        else:
            return jsonify({'job_id': job_id, 'status': 'error', 'error': jobs[job_id].get('error', 'Unknown error')})
    except Exception as e:
        jobs[job_id]['status'] = 'error'
        jobs[job_id]['error'] = str(e)
        return jsonify({'job_id': job_id, 'status': 'error', 'error': str(e)})

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