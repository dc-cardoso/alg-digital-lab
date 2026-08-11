from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Lab(db.Model):
    __tablename__ = 'labs'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    users = db.relationship('User', backref='lab', lazy=True)
    orders = db.relationship('ServiceOrder', backref='lab', lazy=True)

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    lab_id = db.Column(db.Integer, db.ForeignKey('labs.id'), nullable=True) 
    username = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(100)) # Nome real do Dentista ou Admin
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default='dentist') # 'master', 'lab_admin', 'dentist'

class ServiceOrder(db.Model):
    __tablename__ = 'service_orders'
    id = db.Column(db.Integer, primary_key=True)
    os_number = db.Column(db.String(30), unique=True, nullable=False) # Ex: OS-20260810-001
    lab_id = db.Column(db.Integer, db.ForeignKey('labs.id'), nullable=False)
    dentist_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    
    patient_name = db.Column(db.String(100), nullable=False)
    work_type = db.Column(db.String(100), nullable=False) 
    color = db.Column(db.String(20)) 
    is_digital = db.Column(db.Boolean, default=True) 
    notes = db.Column(db.Text)
    
    status = db.Column(db.String(20), default='CREATED') 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    messages = db.relationship('OSMessage', backref='order', lazy=True)
    attachments = db.relationship('OSAttachment', backref='order', lazy=True)

class OSMessage(db.Model):
    __tablename__ = 'os_messages'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('service_orders.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    sender = db.relationship('User')

class OSAttachment(db.Model):
    __tablename__ = 'os_attachments'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('service_orders.id'), nullable=False)
    file_name = db.Column(db.String(255), nullable=False)
    file_key = db.Column(db.String(500), nullable=False) # Caminho no S3/R2 (bucket key)
    file_type = db.Column(db.String(50)) 
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
