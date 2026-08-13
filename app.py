import os
import boto3
import uuid
from botocore.config import Config
from datetime import datetime
from flask import Flask, jsonify, request, render_template
from flask_jwt_extended import (
    JWTManager, create_access_token, jwt_required,
    get_jwt_identity, get_jwt
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from models import db, User, Lab, ServiceOrder, OSMessage, OSAttachment
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

db_url = os.getenv('DATABASE_URL', 'sqlite:///alg_lab.db')
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['JWT_SECRET_KEY'] = os.getenv('JWT_SECRET_KEY', 'dev-secret-key-123')
app.config['JWT_TOKEN_LOCATION'] = ['headers']
# Limite de upload (ex: 50MB para arquivos 3D)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024 

db.init_app(app)
jwt = JWTManager(app)

# S3 / R2 Config
s3_client = boto3.client(
    's3',
    endpoint_url=os.getenv('R2_ENDPOINT_URL'),
    aws_access_key_id=os.getenv('R2_ACCESS_KEY_ID'),
    aws_secret_access_key=os.getenv('R2_SECRET_ACCESS_KEY'),
    config=Config(signature_version='s3v4')
)
R2_BUCKET_NAME = os.getenv('R2_BUCKET_NAME', 'alg-digital-lab')
R2_PUBLIC_URL = os.getenv('R2_PUBLIC_URL', '') # Ex: https://pub-xxxx.r2.dev

with app.app_context():
    db.create_all()
    if not User.query.filter_by(username='master').first():
        master = User(username='master', name='Administrador Master', password_hash=generate_password_hash('master123'), role='master')
        db.session.add(master)
        db.session.commit()

def current_claims():
    identity = get_jwt_identity()
    claims = get_jwt()
    return int(identity), claims.get('role'), claims.get('lab_id'), claims.get('name')

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json(silent=True) or request.form
    user = User.query.filter_by(username=data.get('username')).first()
    if user and check_password_hash(user.password_hash, data.get('password')):
        token = create_access_token(
            identity=str(user.id),
            additional_claims={'role': user.role, 'lab_id': user.lab_id, 'name': user.name}
        )
        return jsonify({'token': token, 'role': user.role, 'name': user.name})
    return jsonify({'error': 'Credenciais inválidas'}), 401

# --- MASTER: GESTÃO DE LABS ---
@app.route('/api/labs', methods=['POST', 'GET'])
@jwt_required()
def manage_labs():
    user_id, role, lab_id, _ = current_claims()
    if role != 'master': return jsonify({'error': 'Acesso negado'}), 403

    if request.method == 'POST':
        data = request.get_json()
        if User.query.filter_by(username=data.get('admin_username')).first():
            return jsonify({'error': 'Usuário já existe'}), 400
        lab = Lab(name=data.get('name'))
        db.session.add(lab)
        db.session.commit()
        admin = User(lab_id=lab.id, username=data.get('admin_username'), name=data.get('admin_name'), password_hash=generate_password_hash(data.get('admin_password')), role='lab_admin')
        db.session.add(admin)
        db.session.commit()
        return jsonify({'message': 'Lab criado!'}), 201

    labs = Lab.query.all()
    return jsonify([{'id': l.id, 'name': l.name} for l in labs])

@app.route('/api/labs/<int:id>', methods=['DELETE'])
@jwt_required()
def delete_lab(id):
    user_id, role, _, _ = current_claims()
    if role != 'master': return jsonify({'error': 'Acesso negado'}), 403
    lab = Lab.query.get_or_404(id)
    db.session.delete(lab)
    db.session.commit()
    return jsonify({'message': 'Laboratório excluído!'})

# --- LAB_ADMIN: GESTÃO DE DENTISTAS ---
@app.route('/api/dentists', methods=['POST', 'GET'])
@jwt_required()
def manage_dentists():
    user_id, role, lab_id, _ = current_claims()
    if role != 'lab_admin': return jsonify({'error': 'Acesso negado'}), 403

    if request.method == 'POST':
        data = request.get_json()
        if User.query.filter_by(username=data.get('username')).first():
            return jsonify({'error': 'Usuário já existe'}), 400
        dentist = User(lab_id=lab_id, username=data.get('username'), name=data.get('name'), password_hash=generate_password_hash(data.get('password')), role='dentist')
        db.session.add(dentist)
        db.session.commit()
        return jsonify({'message': 'Dentista cadastrado!'}), 201

    dentists = User.query.filter_by(lab_id=lab_id, role='dentist').all()
    return jsonify([{'id': d.id, 'name': d.name, 'username': d.username} for d in dentists])

# --- OS: CRUD BÁSICO ---
def generate_os_number(lab_id):
    today_str = datetime.now().strftime("%Y%m%d")
    count = ServiceOrder.query.filter(ServiceOrder.lab_id == lab_id, ServiceOrder.os_number.like(f"OS-{lab_id}-{today_str}-%")).count() + 1
    return f"OS-{lab_id}-{today_str}-{count:03d}"

@app.route('/api/os', methods=['POST', 'GET'])
@jwt_required()
def handle_os():
    user_id, role, lab_id, _ = current_claims()

    if request.method == 'POST':
        if role != 'dentist': return jsonify({'error': 'Apenas dentistas geram OS'}), 403
        data = request.get_json()
        new_os = ServiceOrder(
            os_number=generate_os_number(lab_id),
            lab_id=lab_id, dentist_id=user_id,
            patient_name=data.get('patient_name'), work_type=data.get('work_type'),
            color=data.get('color', ''), is_digital=data.get('is_digital', True),
            notes=data.get('notes', '')
        )
        db.session.add(new_os)
        db.session.commit()
        return jsonify({'message': 'OS gerada!', 'os_id': new_os.id}), 201

    query = ServiceOrder.query.filter_by(dentist_id=user_id) if role == 'dentist' else ServiceOrder.query.filter_by(lab_id=lab_id)
    orders = query.order_by(ServiceOrder.id.desc()).all()
    
    return jsonify([{
        'id': o.id, 'os_number': o.os_number, 'patient_name': o.patient_name,
        'dentist_name': User.query.get(o.dentist_id).name,
        'work_type': o.work_type, 'color': o.color, 'is_digital': o.is_digital,
        'status': o.status, 'notes': o.notes, 'created_at': o.created_at.strftime('%d/%m/%Y %H:%M')
    } for o in orders])

# --- OS: AÇÕES (Cancelamento, Mensagens, Anexos) ---
@app.route('/api/os/<int:os_id>/cancel', methods=['PUT'])
@jwt_required()
def cancel_os(os_id):
    user_id, role, lab_id, _ = current_claims()
    os_obj = ServiceOrder.query.get_or_404(os_id)
    
    if role == 'lab_admin' and os_obj.lab_id == lab_id:
        os_obj.status = 'CANCELLED'
        msg = 'OS cancelada pelo laboratório.'
    elif role == 'dentist' and os_obj.dentist_id == user_id:
        os_obj.status = 'CANCEL_REQUESTED'
        msg = 'Solicitação de cancelamento enviada ao laboratório.'
    else:
        return jsonify({'error': 'Acesso negado'}), 403
        
    db.session.commit()
    return jsonify({'message': msg})

@app.route('/api/os/<int:os_id>/chat', methods=['GET', 'POST'])
@jwt_required()
def os_chat(os_id):
    user_id, role, lab_id, user_name = current_claims()
    os_obj = ServiceOrder.query.get_or_404(os_id)
    
    if request.method == 'POST':
        data = request.get_json()
        msg = OSMessage(os_id=os_id, sender_id=user_id, sender_name=user_name, message=data.get('message'))
        db.session.add(msg)
        db.session.commit()
        return jsonify({'message': 'Mensagem enviada'})
        
    msgs = OSMessage.query.filter_by(os_id=os_id).order_by(OSMessage.created_at.asc()).all()
    return jsonify([{'id': m.id, 'sender': m.sender_name, 'message': m.message, 'time': m.created_at.strftime('%d/%m %H:%M')} for m in msgs])

@app.route('/api/os/<int:os_id>/upload', methods=['POST'])
@jwt_required()
def upload_file(os_id):
    user_id, role, lab_id, _ = current_claims()
    if 'file' not in request.files: return jsonify({'error': 'Nenhum arquivo enviado'}), 400
    
    file = request.files['file']
    filename = secure_filename(f"{uuid.uuid4().hex}_{file.filename}")
    
    try:
        s3_client.upload_fileobj(file, R2_BUCKET_NAME, filename)
        file_url = f"{R2_PUBLIC_URL}/{filename}"
        
        att = OSAttachment(os_id=os_id, file_name=file.filename, file_url=file_url)
        db.session.add(att)
        db.session.commit()
        return jsonify({'message': 'Arquivo anexado com sucesso', 'url': file_url})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/os/<int:os_id>/attachments', methods=['GET'])
@jwt_required()
def get_attachments(os_id):
    atts = OSAttachment.query.filter_by(os_id=os_id).all()
    return jsonify([{'name': a.file_name, 'url': a.file_url} for a in atts])

# --- RELATÓRIOS / FATURAMENTO (Apenas Lab Admin) ---
@app.route('/api/billing', methods=['GET'])
@jwt_required()
def get_billing():
    user_id, role, lab_id, _ = current_claims()
    if role != 'lab_admin': return jsonify({'error': 'Acesso negado'}), 403
    
    # Exemplo simples: Agrupar OS concluidas por dentista
    orders = ServiceOrder.query.filter_by(lab_id=lab_id).all() # Na prática, filtrar por mês
    
    report = {}
    for o in orders:
        if o.dentist_id not in report:
            dentist = User.query.get(o.dentist_id)
            report[o.dentist_id] = {'dentist_name': dentist.name, 'total_os': 0, 'completed': 0}
        report[o.dentist_id]['total_os'] += 1
        if o.status == 'COMPLETED': report[o.dentist_id]['completed'] += 1
            
    return jsonify(list(report.values()))

if __name__ == '__main__':
    app.run(debug=True)
