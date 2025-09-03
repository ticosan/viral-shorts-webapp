from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import os
import json

app = Flask(__name__)
app.config['SECRET_KEY'] = 'viral-shorts-manager-2024-secure-key'
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///shorts_manager.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Modelos de Base de Datos
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), default='editor')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Short(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    dia = db.Column(db.String(10), nullable=False)
    numero = db.Column(db.Integer, nullable=False)
    titulo = db.Column(db.String(200), nullable=False)
    tema = db.Column(db.String(50), nullable=False)
    estado = db.Column(db.String(20), default='pendiente')
    views = db.Column(db.Integer, default=0)
    engagement = db.Column(db.Float, default=0.0)
    url_youtube = db.Column(db.String(200))
    video_fuente_url = db.Column(db.String(200))
    vph_fuente = db.Column(db.Float, default=0.0)
    completado_por = db.Column(db.Integer, db.ForeignKey('user.id'))
    completado_at = db.Column(db.DateTime)
    notas = db.Column(db.Text)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/')
@login_required
def dashboard():
    shorts = Short.query.order_by(Short.dia, Short.numero).all()
    
    # Agrupar por días
    dias_orden = ['lunes', 'martes', 'miercoles', 'jueves', 'viernes', 'sabado', 'domingo']
    shorts_por_dia = {dia: [] for dia in dias_orden}
    
    for short in shorts:
        if short.dia in shorts_por_dia:
            shorts_por_dia[short.dia].append(short)
    
    # Estadísticas
    total_shorts = len(shorts)
    completados = len([s for s in shorts if s.estado == 'completado'])
    en_proceso = len([s for s in shorts if s.estado == 'en_proceso'])
    progreso = (completados / total_shorts * 100) if total_shorts > 0 else 0
    
    stats = {
        'total_shorts': total_shorts,
        'completados': completados,
        'en_proceso': en_proceso,
        'pendientes': total_shorts - completados - en_proceso,
        'progreso': round(progreso, 1)
    }
    
    return render_template('dashboard.html', 
                         shorts_por_dia=shorts_por_dia,
                         dias_orden=dias_orden,
                         stats=stats)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        else:
            flash('Usuario o contraseña incorrectos', 'error')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/short/<int:short_id>')
@login_required
def ver_short(short_id):
    short = Short.query.get_or_404(short_id)
    return render_template('short_detail.html', short=short)

@app.route('/actualizar_short/<int:short_id>', methods=['POST'])
@login_required
def actualizar_short(short_id):
    short = Short.query.get_or_404(short_id)
    
    nuevo_estado = request.json.get('estado')
    views = request.json.get('views', 0)
    engagement = request.json.get('engagement', 0.0)
    url_youtube = request.json.get('url_youtube', '')
    notas = request.json.get('notas', '')
    
    short.estado = nuevo_estado
    short.views = int(views) if views else 0
    short.engagement = float(engagement) if engagement else 0.0
    short.url_youtube = url_youtube
    short.notas = notas
    
    if nuevo_estado == 'completado' and not short.completado_at:
        short.completado_at = datetime.utcnow()
        short.completado_por = current_user.id
    
    db.session.commit()
    
    return jsonify({'success': True, 'mensaje': 'Short actualizado correctamente'})

@app.route('/estadisticas')
@login_required
def estadisticas():
    total_shorts = Short.query.count()
    total_completados = Short.query.filter_by(estado='completado').count()
    total_views = db.session.query(db.func.sum(Short.views)).scalar() or 0
    
    top_shorts = Short.query.filter(Short.views > 0).order_by(Short.views.desc()).limit(10).all()
    
    stats = {
        'total_shorts': total_shorts,
        'total_completados': total_completados,
        'tasa_completado': round((total_completados / total_shorts * 100) if total_shorts > 0 else 0, 1),
        'total_views': total_views,
        'promedio_views': round(total_views / total_completados if total_completados > 0 else 0),
        'top_shorts': top_shorts
    }
    
    return render_template('estadisticas.html', stats=stats)

def init_db():
    with app.app_context():
        db.create_all()
        
        if not User.query.filter_by(username='admin').first():
            admin = User(
                username='admin',
                email='admin@shortsmanager.com',
                password_hash=generate_password_hash('admin123'),
                role='admin'
            )
            db.session.add(admin)
            
            asistente = User(
                username='asistente',
                email='asistente@shortsmanager.com',
                password_hash=generate_password_hash('asistente123'),
                role='editor'
            )
            db.session.add(asistente)
            
            # Crear shorts de ejemplo basados en tu sistema actual
            shorts_data = [
                # LUNES - Nischa Shah (VPH: 2,453)
                {'dia': 'lunes', 'numero': 1, 'titulo': '💰 ERROR Financiero que te Mantiene POBRE', 'tema': 'finanzas', 'vph_fuente': 2453},
                {'dia': 'lunes', 'numero': 2, 'titulo': '🚨 REGLA FINANCIERA que ARRUINA Millones', 'tema': 'finanzas', 'vph_fuente': 2453},
                {'dia': 'lunes', 'numero': 3, 'titulo': '⚠️ TRAMPA de DINERO que NO Conoces', 'tema': 'finanzas', 'vph_fuente': 2453},
                
                # MARTES - Alex Hormozi (VPH: 2,397)
                {'dia': 'martes', 'numero': 1, 'titulo': '🚀 SECRETO de Emprendedor MILLONARIO', 'tema': 'emprendimiento', 'vph_fuente': 2397},
                {'dia': 'martes', 'numero': 2, 'titulo': '💡 ERROR que MATA tu NEGOCIO', 'tema': 'emprendimiento', 'vph_fuente': 2397},
                {'dia': 'martes', 'numero': 3, 'titulo': '🎯 ESTRATEGIA que Usan los RICOS', 'tema': 'emprendimiento', 'vph_fuente': 2397},
                
                # MIÉRCOLES - Sussanne Khan (VPH: 2,297)
                {'dia': 'miercoles', 'numero': 1, 'titulo': '👩‍💼 CEO Mujer REVELA Secretos', 'tema': 'liderazgo', 'vph_fuente': 2297},
                {'dia': 'miercoles', 'numero': 2, 'titulo': '🏢 PROYECTO Millonario OCULTO', 'tema': 'negocios', 'vph_fuente': 2297},
                {'dia': 'miercoles', 'numero': 3, 'titulo': '💎 FAMILIA Empresaria HISTORIA', 'tema': 'emprendimiento', 'vph_fuente': 2297},
                
                # JUEVES - TikToker Finanzas (VPH: 1,570)
                {'dia': 'jueves', 'numero': 1, 'titulo': '🕵️ TikToker EXPUESTA por ESTAFA', 'tema': 'finanzas', 'vph_fuente': 1570},
                {'dia': 'jueves', 'numero': 2, 'titulo': '🚫 VENDEHUMOS Financiero HUMILLADO', 'tema': 'finanzas', 'vph_fuente': 1570},
                {'dia': 'jueves', 'numero': 3, 'titulo': '💸 FRAUDES que DEBES Evitar', 'tema': 'finanzas', 'vph_fuente': 1570},
                
                # VIERNES
                {'dia': 'viernes', 'numero': 1, 'titulo': '🏭 £100M Brand COLAPSÓ así', 'tema': 'negocios', 'vph_fuente': 940},
                {'dia': 'viernes', 'numero': 2, 'titulo': '📉 ERROR que DESTRUYE Empresas', 'tema': 'negocios', 'vph_fuente': 940},
                {'dia': 'viernes', 'numero': 3, 'titulo': '🔄 RECUPERAR Negocio FALLIDO', 'tema': 'negocios', 'vph_fuente': 940},
                
                # SÁBADO  
                {'dia': 'sabado', 'numero': 1, 'titulo': '🍖 IMPERIO Alimentario SECRETOS', 'tema': 'negocios', 'vph_fuente': 851},
                {'dia': 'sabado', 'numero': 2, 'titulo': '🏢 DIVERSIFICAR como los GRANDES', 'tema': 'negocios', 'vph_fuente': 851},
                {'dia': 'sabado', 'numero': 3, 'titulo': '👨‍💼 LÍDER Empresarial HISTORIA', 'tema': 'liderazgo', 'vph_fuente': 851},
                
                # DOMINGO
                {'dia': 'domingo', 'numero': 1, 'titulo': '❤️ AMOR Propio FINANCIERO', 'tema': 'finanzas', 'vph_fuente': 699},
                {'dia': 'domingo', 'numero': 2, 'titulo': '🧘 BIENESTAR Económico MENTAL', 'tema': 'finanzas', 'vph_fuente': 699},
                {'dia': 'domingo', 'numero': 3, 'titulo': '💆 CUIDAR Finanzas como SALUD', 'tema': 'finanzas', 'vph_fuente': 699},
            ]
            
            for short_data in shorts_data:
                short = Short(**short_data)
                db.session.add(short)
            
            db.session.commit()
            print("✅ Base de datos inicializada con 21 shorts")

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))