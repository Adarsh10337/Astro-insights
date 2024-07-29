import csv
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_socketio import SocketIO, join_room, leave_room, emit
import random
import logging
import time
from datetime import datetime, timedelta
from functools import wraps

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key_here'
socketio = SocketIO(app, async_mode='eventlet')

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Mock database of astrologers
astrologers = {
    'Dharminder Pandey': {'status': 'available', 'room': None},
    'Pandit Rajesh Gupta': {'status': 'available', 'room': None},
    'Maya Desai': {'status': 'available', 'room': None}
}

# Store active chats
active_chats = {}


# Add this to your existing imports and global variables
work_history = {}

def read_credentials():
    with open('credentials.csv', 'r') as file:
        reader = csv.DictReader(file)
        return list(reader)

def write_credentials(username, password, role):
    with open('credentials.csv', 'a', newline='') as file:
        writer = csv.writer(file)
        writer.writerow([username, password, role])

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            flash('Please login to access this page', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
@app.route('/index')
def index():
    return render_template('index.html', astrologers=astrologers)

@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username')
    password = request.form.get('password')
    credentials = read_credentials()
    for cred in credentials:
        if cred['username'] == username and cred['password'] == password:
            session['username'] = username
            session['role'] = cred['role']
            if cred['role'] == 'astrologer':
                astrologers[username]['status'] = 'available'
                return redirect(url_for('dashboard'))
            return redirect(url_for('index'))
    flash('Invalid credentials', 'error')
    return redirect(url_for('index'))

@app.route('/register', methods=['POST'])
def register():
    username = request.form.get('username')
    password = request.form.get('password')
    role = request.form.get('role')
    credentials = read_credentials()
    if any(cred['username'] == username for cred in credentials):
        flash('Username already exists', 'error')
        return redirect(url_for('index'))
    write_credentials(username, password, role)
    flash('Registration successful', 'success')
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    username = session.pop('username', None)
    session.pop('role', None)
    if username in astrologers:
        astrologers[username]['status'] = 'offline'
    return redirect(url_for('index'))

@app.route('/call', methods=['POST'])
@login_required
def call():
    astrologer = request.form.get('astrologer')
    if astrologer not in astrologers:
        flash('Invalid astrologer selected.', 'error')
        return redirect(url_for('index'))

    if astrologers[astrologer]['status'] != 'available':
        flash(f'{astrologer} is currently unavailable. Please try again later.', 'error')
        return redirect(url_for('index'))

    room = f"room_{astrologer.lower().replace(' ', '_')}_{random.randint(1000, 9999)}"
    astrologers[astrologer]['status'] = 'busy'
    astrologers[astrologer]['room'] = room
    session['room'] = room
    session['astrologer'] = astrologer

    active_chats[room] = {
        'user': session['username'],
        'astrologer': astrologer,
        'status': 'waiting',
        'messages': [],
        'start_time': time.time()
    }

    logger.info(f"New chat room created: {room}")
    socketio.emit('new_chat', {'user': session['username'], 'astrologer': astrologer, 'room': room}, room=astrologer, namespace='/')

    return redirect(url_for('call_room', astrologer=astrologer))

@app.route('/call_room/<astrologer>')
@login_required
def call_room(astrologer):
    room = session.get('room')
    if not room or room not in active_chats:
        flash('Invalid or expired chat session.', 'error')
        return redirect(url_for('index'))
    
    active_chats[room]['status'] = 'active'
    return render_template('call_room.html', room=room, user_type=session['role'], astrologer=astrologer)

@app.route('/dashboard')
@login_required
def dashboard():
    if session['role'] != 'astrologer':
        flash('Access denied. Astrologer account required.', 'error')
        return redirect(url_for('index'))
    
    astrologer = session['username']
    astrologer_chats = {room: chat for room, chat in active_chats.items() if chat['astrologer'] == astrologer}
    return render_template('dashboard.html', astrologer=astrologer, active_chats=astrologer_chats)

@app.route('/get_chat_history/<room>')
@login_required
def get_chat_history(room):
    if room not in active_chats:
        return jsonify([])
    return jsonify(active_chats[room].get('messages', []))


@socketio.on('join')
def on_join(data):
    room = data['room']
    join_room(room)
    logger.info(f"User {session['username']} joined room {room}")
    
    if room in active_chats:
        active_chats[room]['status'] = 'active'
        emit('status', {'msg': f"{session['username']} has joined the call."}, room=room)
        
        # Send chat history to the joining user
        chat_history = active_chats[room].get('messages', [])
        emit('chat_history', {'messages': chat_history})
    else:
        logger.warning(f"Room {room} not found in active_chats")
        emit('error', {'msg': 'Chat room not found or has expired.'})

@socketio.on('message')
def handle_message(data):
    room = data['room']
    if room and room in active_chats:
        message = {
            'msg': data['msg'],
            'user': session['username'],
            'timestamp': data.get('timestamp', time.time()),
            'room': room
        }
        active_chats[room]['messages'].append(message)
        # Broadcast the message to everyone in the room, including the sender
        emit('message', message, room=room, broadcast=True)
        logger.info(f"Message sent in room {room}: {message}")

def update_active_chats(room, message):
    if room in active_chats:
        active_chats[room]['messages'].append(message)

def ack_message(data):
    logger.info(f"Message acknowledged: {data}")

def end_chat(room):
    if room in active_chats:
        astrologer = active_chats[room]['astrologer']
        user = active_chats[room]['user']
        start_time = active_chats[room].get('start_time')
        logger.info(f"Ending chat in room {room} between astrologer {astrologer} and user {user}")
        
        astrologers[astrologer]['status'] = 'available'
        astrologers[astrologer]['room'] = None
        socketio.emit('chat_ended', room=room)
        socketio.emit('astrologer_status_update', {'astrologer': astrologer, 'status': 'available'})
        
        if start_time:
            duration = time.time() - start_time
            if astrologer not in work_history:
                work_history[astrologer] = []
            work_history[astrologer].append({
                'user': user,
                'duration': duration,
                'end_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
            logger.info(f"Chat duration for {astrologer} with {user}: {duration:.2f} seconds")
        
        del active_chats[room]
    
    socketio.emit('update_chats')

@socketio.on('leave_chat')
def handle_leave_chat(data):
    room = data['room']
    logger.debug(f"Leave chat event triggered by {session.get('username')} with role {session.get('role')}")
    if room in active_chats:
        leave_room(room)
        emit('user_left', {'user': session['username']}, room=room)
        if session['role'] == 'user':
            end_chat(room)
        else:
            emit('astrologer_left', room=room)

@socketio.on('disconnect')
def handle_disconnect():
    logger.info(f"Disconnect event for user {session.get('username')} with role {session.get('role')}")
    if 'username' in session:
        if session['role'] == 'astrologer':
            astrologers[session['username']]['status'] = 'offline'
            socketio.emit('astrologer_status_update', {'astrologer': session['username'], 'status': 'offline'})
        else:
            end_chat(session.get('room'))

def end_chat(room):
    if room in active_chats:
        astrologer = active_chats[room]['astrologer']
        user = active_chats[room]['user']
        logger.info(f"Ending chat in room {room} between astrologer {astrologer} and user {user}")
        
        astrologers[astrologer]['status'] = 'available'
        astrologers[astrologer]['room'] = None
        socketio.emit('chat_ended', room=room)
        socketio.emit('astrologer_status_update', {'astrologer': astrologer, 'status': 'available'})
        
        del active_chats[room]

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

@app.route('/work_history/<astrologer>')
@login_required
def get_work_history(astrologer):
    if astrologer in work_history:
        return jsonify(work_history[astrologer])
    return jsonify([])

@socketio.on('connect')
def on_connect():
    if 'username' in session:
        if session['role'] == 'astrologer':
            join_room(session['username'])
        emit('connection_success', {'status': 'connected'})

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=8080, debug=True)