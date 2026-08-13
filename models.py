from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Lab(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    users = db.relationship('User', backref='lab', lazy=True, cascade="all, delete-orphan")
    orders = db.relationship('ServiceOrder', backref='lab', lazy=True, cascade="all, delete-orphan")

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lab_id = db.Column(db.Integer, db.ForeignKey('lab.id'), nullable=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False) # 'master', 'lab_admin', 'dentist'

class ServiceOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    os_number = db.Column(db.String(50), nullable=False, unique=True)
    lab_id = db.Column(db.Integer, db.ForeignKey('lab.id'), nullable=False)
    dentist_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    patient_name = db.Column(db.String(100), nullable=False)
    work_type = db.Column(db.String(100), nullable=False)
    color = db.Column(db.String(50))
    is_digital = db.Column(db.Boolean, default=True)
    notes = db.Column(db.Text)
    status = db.Column(db.String(20), default='PENDING') # PENDING, IN_PROGRESS, CANCEL_REQUESTED, CANCELLED, COMPLETED
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    messages = db.relationship('OSMessage', backref='order', lazy=True, cascade="all, delete-orphan")
    attachments = db.relationship('OSAttachment', backref='order', lazy=True, cascade="all, delete-orphan")

class OSMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    os_id = db.Column(db.Integer, db.ForeignKey('service_order.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    sender_name = db.Column(db.String(100)) # Desnormalizado para facilitar exibição
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class OSAttachment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    os_id = db.Column(db.Integer, db.ForeignKey('service_order.id'), nullable=False)
    file_name = db.Column(db.String(255), nullable=False)
    file_url = db.Column(db.String(500), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
