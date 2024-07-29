import csv
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_socketio import SocketIO, join_room, leave_room, emit
import random
import logging
import time
from datetime import datetime, timedelta
from functools import wraps
from collections import deque
import json
import os
from werkzeug.utils import secure_filename
from datetime import timedelta
from functools import wraps

UPLOAD_FOLDER = 'static/images'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

app = Flask(__name__, static_folder='static')
app.config['SECRET_KEY'] = 'your_secret_key_here'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
socketio = SocketIO(app, async_mode='eventlet')

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


active_chats = {}
work_history = {}

def read_credentials():
    credentials = {}
    try:
        with open('credentials.csv', 'r') as file:
            reader = csv.DictReader(file)
            for row in reader:
                image_filename = row.get('image_path', '').strip()
                image_path = f'images/{image_filename}' if image_filename else None
                credentials[row['username']] = {
                    'password': row['password'],
                    'role': row['role'],
                    'name': row.get('name', row['username']),
                    'experience': row.get('experience', ''),
                    'details': row.get('details', ''),
                    'expertise': row.get('expertise', ''),
                    'image_path': image_path,
                    'status': 'available',
                    'room': None,
                    'queue': deque()
                }
    except FileNotFoundError:
        print("Warning: credentials.csv file not found.")
    except Exception as e:
        print(f"Error reading credentials.csv: {str(e)}")
    return credentials

# Add this function to refresh astrologers data
def refresh_astrologers():
    global astrologers
    astrologers = read_credentials()

def write_credentials(username, password, role, name='', experience='', details='', expertise='', image_filename=''):
    with open('credentials.csv', 'a', newline='') as file:
        writer = csv.writer(file)
        writer.writerow([username, password, role, name, experience, details, expertise, image_filename])

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            return redirect(url_for('index', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

@app.before_request
def make_session_permanent():
    session.permanent = True
    app.permanent_session_lifetime = timedelta(days=7)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/upload_astrologer', methods=['GET', 'POST'])
@login_required
def upload_astrologer():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        name = request.form.get('name')
        experience = request.form.get('experience')
        details = request.form.get('details')
        expertise = request.form.get('expertise')
        file = request.files['image']

        filename = ''
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)

        write_credentials(username, password, 'astrologer', name, experience, details, expertise, filename)

        flash('Astrologer added successfully', 'success')
        return redirect(url_for('main'))

    return render_template('upload_astrologer.html')

astrologers = read_credentials()

@app.route('/')
@app.route('/index')
def index():
    if 'username' in session:
        if session['role'] == 'astrologer':
            return redirect(url_for('dashboard'))
        return redirect(url_for('main'))
    return render_template('index.html', astrologers=astrologers)

@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username')
    password = request.form.get('password')
    app.logger.debug(f"Login attempt for username: {username}")
    
    credentials = read_credentials()
    app.logger.debug(f"Number of credentials read: {len(credentials)}")
    
    if username in credentials and credentials[username]['password'] == password:
        session.permanent = True
        session['username'] = username
        session['role'] = credentials[username]['role']
        app.logger.info(f"Successful login for {username} with role {credentials[username]['role']}")
        if credentials[username]['role'] == 'astrologer':
            return redirect(url_for('dashboard'))
        return redirect(url_for('main'))
    
    app.logger.warning(f"Failed login attempt for {username}")
    flash('Invalid credentials', 'error')
    return redirect(url_for('index'))

@app.route('/register', methods=['POST'])
def register():
    username = request.form.get('username')
    password = request.form.get('password')
    role = request.form.get('role', 'user')  # Default to 'user' if not specified
    credentials = read_credentials()
    if any(cred['username'] == username for cred in credentials):
        flash('Username already exists', 'error')
        return redirect(url_for('index'))
    write_credentials(username, password, role)
    session['username'] = username
    session['role'] = role
    flash('Registration successful', 'success')
    if role == 'astrologer':
        return redirect(url_for('dashboard'))
    return redirect(url_for('main'))

@app.route('/main')
@login_required
def main():
    refresh_astrologers()  # Refresh astrologers data before rendering the page
    return render_template('main.html', astrologers=astrologers)


@app.route('/logout')
def logout():
    username = session.pop('username', None)
    session.pop('role', None)
    if username in astrologers:
        astrologers[username]['status'] = 'offline'
    flash('You have been logged out', 'info')
    return redirect(url_for('index'))

@app.route('/call', methods=['POST'])
@login_required
def call():
    astrologer_username = request.form.get('astrologer')
    if astrologer_username not in astrologers:
        flash('Invalid astrologer selected or astrologer is not available.', 'error')
        return redirect(url_for('main'))

    astrologer = astrologers[astrologer_username]
    if astrologer['status'] == 'busy':
        astrologer['queue'].append(session['username'])
        flash(f'You have been added to the waiting list for {astrologer["name"]}.', 'info')
        return redirect(url_for('waiting_room', astrologer=astrologer_username))

    timestamp = int(time.time())
    room = f"room_{astrologer_username.lower().replace(' ', '_')}_{timestamp}_{random.randint(1000, 9999)}"
    astrologer['status'] = 'busy'
    astrologer['room'] = room
    session['room'] = room
    session['astrologer'] = astrologer_username

    active_chats[room] = {
        'user': session['username'],
        'astrologer': astrologer_username,
        'status': 'waiting',
        'messages': [],
        'start_time': time.time(),
        'astrologer_joined': False,
        'user_notes': ''
    }

    logger.info(f"New chat room created: {room}")
    socketio.emit('new_chat', {'user': session['username'], 'astrologer': astrologer_username, 'room': room}, room=astrologer_username, namespace='/')
    socketio.emit('update_active_chats', {'room': room, 'chat': active_chats[room]}, namespace='/')

    return redirect(url_for('call_room', astrologer=astrologer_username))

@app.route('/waiting_room/<astrologer>')
@login_required
def waiting_room(astrologer):
    queue_position = list(astrologers[astrologer]['queue']).index(session['username']) + 1
    return render_template('waiting_room.html', astrologer=astrologer, queue_position=queue_position)

@app.route('/call_room/<astrologer>')
@login_required
def call_room(astrologer):
    if session['role'] != 'user':
        flash('Access denied. User account required.', 'error')
        return redirect(url_for('index'))
    
    room = session.get('room') or f"room_{astrologer.lower().replace(' ', '_')}_{int(time.time())}_{random.randint(1000, 9999)}"
    session['room'] = room
    session['astrologer'] = astrologer

    if room not in active_chats:
        active_chats[room] = {
            'user': session['username'],
            'astrologer': astrologer,
            'status': 'waiting',
            'messages': [],
            'start_time': time.time(),
            'astrologer_joined': False,
            'user_notes': ''
        }

        logger.info(f"New chat room created: {room}")
        socketio.emit('new_chat', {'user': session['username'], 'astrologer': astrologer, 'room': room}, room=astrologer)
        socketio.emit('update_active_chats', {'room': room, 'chat': active_chats[room]}, namespace='/')

    return render_template('call_room.html', room=room, user_type='user', astrologer=astrologer)

@app.route('/dashboard')
@login_required
def dashboard():
    if session['role'] != 'astrologer':
        flash('Access denied. Astrologer account required.', 'error')
        return redirect(url_for('index'))
    
    astrologer = session['username']
    astrologer_chats = {room: chat for room, chat in active_chats.items() if chat['astrologer'] == astrologer}
    queue_length = len(astrologers.get(astrologer, {}).get('queue', []))
    astrologer_work_history = work_history.get(astrologer, [])
    
    return render_template('dashboard.html', astrologer=astrologer, active_chats=astrologer_chats, 
                           queue_length=queue_length, work_history=astrologer_work_history)

@app.route('/astrologer_dashboard')
@login_required
def astrologer_dashboard():
    if session['role'] != 'astrologer':
        flash('Access denied', 'error')
        return redirect(url_for('main'))
    
    # Here you can add logic to fetch astrologer-specific data
    # For example, upcoming appointments, recent reviews, etc.
    
    return render_template('astrologer_dashboard.html', astrologer=session['username'])


@socketio.on('join')
def on_join(data):
    room = data['room']
    join_room(room)
    logger.info(f"User {session['username']} joined room {room}")
    
    if room in active_chats:
        active_chats[room]['status'] = 'active'
        if session['role'] == 'astrologer':
            active_chats[room]['astrologer_joined'] = True
        emit('status', {'msg': f"{session['username']} has joined the call."}, room=room)
        
        chat_history = active_chats[room].get('messages', [])
        emit('chat_history', {'messages': chat_history})
        
        if session['role'] == 'astrologer':
            emit('astrologer_joined', room=room)
    else:
        logger.warning(f"Room {room} not found in active_chats")
        emit('error', {'msg': 'Chat room not found or has expired.'})

@socketio.on('message')
def handle_message(data):
    room = data['room']
    if room and room in active_chats:
        if session['role'] == 'user' and not active_chats[room]['astrologer_joined']:
            emit('error', {'msg': 'Please wait for the astrologer to join before sending messages.'})
            return
        
        message = {
            'msg': data['msg'],
            'user': session['username'],
            'timestamp': data.get('timestamp', time.time()),
            'room': room
        }
        active_chats[room]['messages'].append(message)
        emit('message', message, room=room, broadcast=True)
        logger.info(f"Message sent in room {room}: {message}")

@app.route('/save_user_notes', methods=['POST'])
@login_required
def save_user_notes():
    if session['role'] != 'astrologer':
        return jsonify({'success': False, 'message': 'Only astrologers can save notes'}), 403
    
    data = request.json
    room = data.get('room')
    notes = data.get('notes')
    
    if not room or not notes:
        return jsonify({'success': False, 'message': 'Missing room or notes'}), 400
    
    if room not in active_chats:
        return jsonify({'success': False, 'message': 'Chat room not found'}), 404
    
    active_chats[room]['user_notes'] = notes
    logger.info(f"User notes saved for room {room}")
    
    return jsonify({'success': True, 'message': 'Notes saved successfully'})

@app.route('/get_queue', methods=['GET'])
@login_required
def get_queue():
    if session['role'] != 'astrologer':
        return jsonify({'success': False, 'message': 'Access denied'}), 403
    
    astrologer = session['username']
    queue = list(astrologers[astrologer]['queue'])
    return jsonify({'success': True, 'queue': queue})

@app.route('/remove_from_queue', methods=['POST'])
@login_required
def remove_from_queue():
    if session['role'] != 'astrologer':
        return jsonify({'success': False, 'message': 'Access denied'}), 403
    
    astrologer = session['username']
    user_to_remove = request.json.get('user')
    
    if user_to_remove in astrologers[astrologer]['queue']:
        astrologers[astrologer]['queue'].remove(user_to_remove)
        return jsonify({'success': True, 'message': f'Removed {user_to_remove} from queue'})
    else:
        return jsonify({'success': False, 'message': 'User not found in queue'}), 404

@app.route('/get_work_history', methods=['GET'])
@login_required
def get_work_history():
    if session['role'] != 'astrologer':
        return jsonify({'success': False, 'message': 'Access denied'}), 403
    
    astrologer = session['username']
    astrologer_work_history = work_history.get(astrologer, [])
    return jsonify({'success': True, 'work_history': astrologer_work_history})

@socketio.on('leave_chat')
def handle_leave_chat(data):
    room = data['room']
    user = data.get('user') or session.get('username')
    logger.debug(f"Leave chat event triggered by {user} for room {room}")
    if room in active_chats:
        leave_room(room)
        end_chat(room, user)

def end_chat(room, leaving_user):
    if room in active_chats:
        astrologer = active_chats[room]['astrologer']
        user = active_chats[room]['user']
        start_time = active_chats[room].get('start_time')
        logger.info(f"Ending chat in room {room} between astrologer {astrologer} and user {user}")
        
        # Notify both parties that the chat has ended
        socketio.emit('chat_ended', {'room': room, 'leaving_user': leaving_user}, room=room)
        
        if astrologer in astrologers:
            astrologers[astrologer]['status'] = 'available'
            astrologers[astrologer]['room'] = None
            socketio.emit('astrologer_status_update', {'astrologer': astrologer, 'status': 'available'})
        
        if start_time:
            duration = time.time() - start_time
            if astrologer not in work_history:
                work_history[astrologer] = []
            work_history[astrologer].append({
                'user': user,
                'duration': duration,
                'end_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'notes': active_chats[room].get('user_notes', '')
            })
            logger.info(f"Chat duration for {astrologer} with {user}: {duration:.2f} seconds")
        
        del active_chats[room]
        
        if astrologer in astrologers and astrologers[astrologer]['queue']:
            next_user = astrologers[astrologer]['queue'].popleft()
            socketio.emit('chat_available', {'astrologer': astrologer}, room=next_user)
    
    socketio.emit('update_active_chats', {'active_chats': active_chats}, namespace='/')

@app.route('/favicon.ico')
def favicon():
    return '', 204

@socketio.on('disconnect')
def handle_disconnect():
    username = session.get('username')
    role = session.get('role')
    logger.info(f"Disconnect event for user {username} with role {role}")
    
    if username:
        if role == 'astrologer' and username in astrologers:
            astrologers[username]['status'] = 'offline'
            socketio.emit('astrologer_status_update', {'astrologer': username, 'status': 'offline'})
        
        if 'room' in session:
            end_chat(session['room'], username)
    
    socketio.emit('update_chats')

@app.route('/update_astrologer_status', methods=['POST'])
@login_required
def update_astrologer_status():
    astrologer = request.form.get('astrologer')
    status = request.form.get('status')
    if astrologer in astrologers:
        astrologers[astrologer]['status'] = status
        socketio.emit('astrologer_status_update', {'astrologer': astrologer, 'status': status})
        return jsonify({'success': True})
    return jsonify({'success': False}), 400

@socketio.on('connect')
def on_connect():
    if 'username' in session:
        if session['role'] == 'astrologer':
            join_room(session['username'])
        emit('connection_success', {'status': 'connected'})

@app.route('/get_active_chats', methods=['GET'])
@login_required
def get_active_chats():
    if session['role'] != 'astrologer':
        return jsonify({'success': False, 'message': 'Access denied'}), 403
    
    astrologer = session['username']
    astrologer_chats = {room: chat for room, chat in active_chats.items() if chat['astrologer'] == astrologer and chat['status'] != 'ended'}
    return jsonify({'success': True, 'active_chats': astrologer_chats})

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=8080, debug=True)