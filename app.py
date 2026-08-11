import os
import boto3
from botocore.config import Config
from datetime import datetime
from flask import Flask, jsonify, request, send_file
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from werkzeug.security import generate_password_hash
from io import BytesIO
from fpdf import FPDF
from models import db, User, Lab, ServiceOrder, OSMessage, OSAttachment
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///alg_lab.db')
app.config['JWT_SECRET_KEY'] = os.getenv('JWT_SECRET_KEY', 'dev-secret-key-123')
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

def generate_os_number(lab_id):
    today_str = datetime.now().strftime("%Y%m%d")
    count = ServiceOrder.query.filter(
        ServiceOrder.lab_id == lab_id,
        ServiceOrder.os_number.like(f"OS-{lab_id}-{today_str}-%")
    ).count() + 1
    return f"OS-{lab_id}-{today_str}-{count:03d}"

# --- GESTÃO DE ARQUIVOS (UPLOAD & DOWNLOAD DIRETO COM S3/R2) ---

@app.route('/api/os/<int:os_id>/attachments/presigned-upload', methods=['POST'])
@jwt_required()
def generate_presigned_upload(os_id):
    '''
    Passo 1: O Frontend pede permissão para enviar um arquivo.
    Retornamos uma URL segura onde o próprio frontend/app fará o upload (ZERO custo na nossa API).
    '''
    current_user = get_jwt_identity()
    os_obj = ServiceOrder.query.filter_by(id=os_id, lab_id=current_user['lab_id']).first_or_404()
    
    data = request.json
    file_name = data.get('file_name', 'arquivo.stl')
    content_type = data.get('content_type', 'application/octet-stream')
    
    # Organização no Bucket: lab_<id>/os_<id>/timestamp_filename
    file_key = f"lab_{current_user['lab_id']}/os_{os_id}/{int(datetime.utcnow().timestamp())}_{file_name}"
    
    try:
        presigned_url = s3_client.generate_presigned_url(
            'put_object',
            Params={'Bucket': R2_BUCKET_NAME, 'Key': file_key, 'ContentType': content_type},
            ExpiresIn=900 # O link de upload vale por 15 minutos
        )
        return jsonify({'upload_url': presigned_url, 'file_key': file_key})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/os/<int:os_id>/attachments', methods=['POST'])
@jwt_required()
def confirm_upload(os_id):
    '''
    Passo 2: Após o Flutter/Web enviar o arquivo pro S3/R2 com sucesso,
    ele chama essa rota para salvar no nosso banco de dados.
    '''
    current_user = get_jwt_identity()
    os_obj = ServiceOrder.query.filter_by(id=os_id, lab_id=current_user['lab_id']).first_or_404()
    
    data = request.json
    attachment = OSAttachment(
        order_id=os_obj.id,
        file_name=data['file_name'],
        file_key=data['file_key'],
        file_type=data.get('file_type', 'unknown')
    )
    db.session.add(attachment)
    db.session.commit()
    return jsonify({'message': 'Anexo registrado com sucesso.'}), 201

@app.route('/api/os/attachment/<int:attachment_id>/download', methods=['GET'])
@jwt_required()
def download_attachment(attachment_id):
    '''
    Gera link de download direto do S3/R2 se o arquivo tiver menos de 30 dias.
    '''
    current_user = get_jwt_identity()
    attachment = OSAttachment.query.get_or_404(attachment_id)
    os_obj = ServiceOrder.query.get_or_404(attachment.order_id)
    
    if os_obj.lab_id != current_user['lab_id']:
        return jsonify({'error': 'Acesso negado.'}), 403

    # TRAVA DE SEGURANÇA: 30 DIAS (Regra de Negócio)
    dias_passados = (datetime.utcnow() - attachment.uploaded_at).days
    if dias_passados > 30:
        return jsonify({
            'error': 'FILE_EXPIRED',
            'message': 'Este arquivo expirou (limite de 30 dias). Solicite o reenvio do trabalho ao doutor.'
        }), 410 # HTTP 410 Gone

    try:
        presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': R2_BUCKET_NAME, 'Key': attachment.file_key},
            ExpiresIn=3600 # Link de download expira em 1 hora
        )
        return jsonify({'download_url': presigned_url})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# --- RESTO DAS ROTAS (OS, MENSAGENS, PDF) ---
# ... (Mantidas conforme combinado)

if __name__ == '__main__':
    app.run(debug=True)
