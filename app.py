import os
import boto3
from botocore.config import Config
from datetime import datetime
from flask import Flask, jsonify, request, render_template
from flask_jwt_extended import (
    JWTManager, create_access_token, jwt_required,
    get_jwt_identity, get_jwt
)
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, Lab, ServiceOrder, OSMessage, OSAttachment
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# Correção URL Supabase (postgres:// -> postgresql://)
db_url = os.getenv('DATABASE_URL', 'sqlite:///alg_lab.db')
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['JWT_SECRET_KEY'] = os.getenv('JWT_SECRET_KEY', 'dev-secret-key-123')

# Token só via header Authorization (padrão seguro). Query string removida
# porque expõe o JWT em logs de acesso (Render, proxies, Cloudflare etc).
app.config['JWT_TOKEN_LOCATION'] = ['headers']

db.init_app(app)
jwt = JWTManager(app)

# CONFIGURAÇÃO CLOUDFLARE R2 / AWS S3
s3_client = boto3.client(
    's3',
    endpoint_url=os.getenv('R2_ENDPOINT_URL'),
    aws_access_key_id=os.getenv('R2_ACCESS_KEY_ID'),
    aws_secret_access_key=os.getenv('R2_SECRET_ACCESS_KEY'),
    config=Config(signature_version='s3v4')
)
R2_BUCKET_NAME = os.getenv('R2_BUCKET_NAME', 'alg-digital-lab')

with app.app_context():
    db.create_all()
    if not User.query.filter_by(username='master').first():
        master = User(
            username='master',
            name='Administrador Master',
            password_hash=generate_password_hash('master123'),
            role='master'
        )
        db.session.add(master)
        db.session.commit()


def current_claims():
    """Helper: devolve (user_id:int, role:str, lab_id:int|None) a partir do JWT."""
    identity = get_jwt_identity()          # agora é só o ID (string)
    claims = get_jwt()                     # aqui vêm role/lab_id/name
    return int(identity), claims.get('role'), claims.get('lab_id')


# --- ROTA DE PÁGINA INICIAL (FRONTEND) ---
@app.route('/')
def index():
    return render_template('index.html')


# --- AUTENTICAÇÃO ---
@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json(silent=True) or request.form
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return jsonify({'error': 'Usuário e senha são obrigatórios'}), 400

    user = User.query.filter_by(username=username).first()
    if user and check_password_hash(user.password_hash, password):
        # ATENÇÃO: identity precisa ser STRING (não dict). Versões recentes
        # do PyJWT exigem que o claim "sub" seja string, senão o decode
        # falha com 422 em toda rota protegida (era a causa do bug de login).
        token = create_access_token(
            identity=str(user.id),
            additional_claims={
                'role': user.role,
                'lab_id': user.lab_id,
                'name': user.name
            }
        )
        return jsonify({'token': token, 'role': user.role, 'name': user.name})

    return jsonify({'error': 'Credenciais inválidas'}), 401


# --- GESTÃO DE LABORATÓRIOS (Apenas Master) ---
@app.route('/api/labs', methods=['POST', 'GET'])
@jwt_required()
def manage_labs():
    user_id, role, lab_id = current_claims()
    if role != 'master':
        return jsonify({'error': 'Acesso negado'}), 403

    if request.method == 'POST':
        data = request.get_json(silent=True) or request.form
        name = data.get('name')
        admin_name = data.get('admin_name')
        admin_username = data.get('admin_username')
        admin_password = data.get('admin_password')

        if not all([name, admin_name, admin_username, admin_password]):
            return jsonify({'error': 'Todos os campos são obrigatórios'}), 400

        if User.query.filter_by(username=admin_username).first():
            return jsonify({'error': 'Este nome de usuário já está em uso'}), 400

        lab = Lab(name=name)
        db.session.add(lab)
        db.session.commit()

        admin_user = User(
            lab_id=lab.id,
            username=admin_username,
            name=admin_name,
            password_hash=generate_password_hash(admin_password),
            role='lab_admin'
        )
        db.session.add(admin_user)
        db.session.commit()
        return jsonify({'message': 'Laboratório e Admin criados com sucesso!'}), 201

    labs = Lab.query.all()
    return jsonify([{'id': l.id, 'name': l.name} for l in labs])


# --- GESTÃO DE CLIENTES / DENTISTAS (Apenas Lab Admin) ---
@app.route('/api/dentists', methods=['POST', 'GET'])
@jwt_required()
def manage_dentists():
    user_id, role, lab_id = current_claims()
    if role != 'lab_admin':
        return jsonify({'error': 'Acesso negado'}), 403

    if request.method == 'POST':
        data = request.get_json(silent=True) or request.form
        username = data.get('username')
        name = data.get('name')
        password = data.get('password')

        if not all([username, name, password]):
            return jsonify({'error': 'Todos os campos são obrigatórios'}), 400

        if User.query.filter_by(username=username).first():
            return jsonify({'error': 'Este nome de usuário já está em uso'}), 400

        dentist = User(
            lab_id=lab_id,
            username=username,
            name=name,
            password_hash=generate_password_hash(password),
            role='dentist'
        )
        db.session.add(dentist)
        db.session.commit()
        return jsonify({'message': 'Cliente/Dentista cadastrado com sucesso!'}), 201

    dentists = User.query.filter_by(lab_id=lab_id, role='dentist').all()
    return jsonify([{'id': d.id, 'name': d.name, 'username': d.username} for d in dentists])


# --- ORDEM DE SERVIÇO (OS) ---
def generate_os_number(lab_id):
    today_str = datetime.now().strftime("%Y%m%d")
    count = ServiceOrder.query.filter(
        ServiceOrder.lab_id == lab_id,
        ServiceOrder.os_number.like(f"OS-{lab_id}-{today_str}-%")
    ).count() + 1
    return f"OS-{lab_id}-{today_str}-{count:03d}"


@app.route('/api/os', methods=['POST', 'GET'])
@jwt_required()
def handle_os():
    user_id, role, lab_id = current_claims()

    if request.method == 'POST':
        if role != 'dentist':
            return jsonify({'error': 'Apenas clientes/dentistas podem gerar OS'}), 403
        data = request.get_json(silent=True) or request.form

        patient_name = data.get('patient_name')
        work_type = data.get('work_type')
        if not all([patient_name, work_type]):
            return jsonify({'error': 'Nome do paciente e tipo de trabalho são obrigatórios'}), 400

        new_os = ServiceOrder(
            os_number=generate_os_number(lab_id),
            lab_id=lab_id,
            dentist_id=user_id,
            patient_name=patient_name,
            work_type=work_type,
            color=data.get('color', ''),
            is_digital=str(data.get('is_digital', True)).lower() in ['true', '1', 'yes'],
            notes=data.get('notes', '')
        )
        db.session.add(new_os)
        db.session.commit()
        return jsonify({
            'message': 'OS gerada com sucesso!',
            'os_id': new_os.id,
            'os_number': new_os.os_number
        }), 201

    if role == 'dentist':
        orders = ServiceOrder.query.filter_by(dentist_id=user_id).all()
    else:
        orders = ServiceOrder.query.filter_by(lab_id=lab_id).all()

    return jsonify([{
        'id': o.id, 'os_number': o.os_number, 'patient_name': o.patient_name,
        'work_type': o.work_type, 'color': o.color, 'is_digital': o.is_digital,
        'status': o.status, 'created_at': o.created_at.strftime('%d/%m/%Y %H:%M')
    } for o in orders])


@app.route('/api/os/<int:os_id>/cancel', methods=['PUT'])
@jwt_required()
def cancel_os(os_id):
    user_id, role, lab_id = current_claims()
    if role != 'lab_admin':
        return jsonify({'error': 'Apenas o laboratório pode cancelar uma OS.'}), 403
    os_obj = ServiceOrder.query.filter_by(id=os_id, lab_id=lab_id).first_or_404()
    os_obj.status = 'CANCELLED'
    db.session.commit()
    return jsonify({'message': 'OS cancelada com sucesso.'})


if __name__ == '__main__':
    app.run(debug=True)
