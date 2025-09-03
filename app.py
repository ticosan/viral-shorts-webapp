from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import os
import json
import requests
import openai
from anthropic import Anthropic

app = Flask(__name__)
app.config['SECRET_KEY'] = 'viral-shorts-manager-2024-secure-key'
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///shorts_manager.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# API Keys configuration
app.config['YOUTUBE_API_KEY'] = os.environ.get('YOUTUBE_API_KEY', 'AIzaSyCtkIhtKBx14kzaHgTGltiSAigbmV-WcyE')
app.config['OPENAI_API_KEY'] = os.environ.get('OPENAI_API_KEY', '')
app.config['ANTHROPIC_API_KEY'] = os.environ.get('ANTHROPIC_API_KEY', '')

# Initialize AI clients
if app.config['OPENAI_API_KEY']:
    openai.api_key = app.config['OPENAI_API_KEY']

if app.config['ANTHROPIC_API_KEY']:
    anthropic_client = Anthropic(api_key=app.config['ANTHROPIC_API_KEY'])
else:
    anthropic_client = None

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

class Semana(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    numero_semana = db.Column(db.Integer, nullable=False)  # 1, 2, 3, etc.
    mes = db.Column(db.String(20), nullable=False)  # 'Septiembre', 'Octubre', etc.
    año = db.Column(db.Integer, nullable=False)  # 2025
    fecha_inicio = db.Column(db.Date, nullable=False)  # Lunes de la semana
    fecha_fin = db.Column(db.Date, nullable=False)  # Domingo de la semana
    estado = db.Column(db.String(20), default='planificacion')  # planificacion, activa, completada
    videos_objetivo = db.Column(db.Integer, default=3)  # Cuántos shorts planear
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relación con shorts
    shorts = db.relationship('Short', backref='semana_obj', lazy=True, cascade='all, delete-orphan')

class Short(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    semana_id = db.Column(db.Integer, db.ForeignKey('semana.id'), nullable=False)
    dia_publicacion = db.Column(db.Date, nullable=False)  # Fecha específica de publicación
    dia_nombre = db.Column(db.String(10), nullable=False)  # 'lunes', 'martes', etc.
    orden_dia = db.Column(db.Integer, default=1)  # Por si hay múltiples videos el mismo día
    titulo = db.Column(db.String(200), nullable=False)
    tema = db.Column(db.String(50), nullable=False)
    estado = db.Column(db.String(20), default='investigacion')  # investigacion, guion_generado, en_proceso, completado, cancelado
    views = db.Column(db.Integer, default=0)
    engagement = db.Column(db.Float, default=0.0)
    url_youtube = db.Column(db.String(200))
    video_fuente_url = db.Column(db.String(200))
    video_fuente_id = db.Column(db.String(50))  # YouTube video ID
    vph_fuente = db.Column(db.Float, default=0.0)
    guion_generado = db.Column(db.Text)  # Guión generado por IA
    video_descargado = db.Column(db.Boolean, default=False)
    completado_por = db.Column(db.Integer, db.ForeignKey('user.id'))
    completado_at = db.Column(db.DateTime)
    notas = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/')
@login_required
def dashboard():
    # Obtener semana específica o semana actual
    semana_id = request.args.get('semana_id')
    
    if semana_id:
        semana_actual = Semana.query.get(semana_id)
    else:
        # Obtener semana actual o más reciente
        semana_actual = Semana.query.filter(
            Semana.fecha_inicio <= datetime.now().date(),
            Semana.fecha_fin >= datetime.now().date()
        ).first()
        
        if not semana_actual:
            # Si no hay semana actual, buscar la más reciente
            semana_actual = Semana.query.order_by(Semana.fecha_inicio.desc()).first()
    
    # Si no existe ninguna semana, crear la semana actual
    if not semana_actual:
        semana_actual = crear_semana_actual()
    
    # Obtener todas las semanas para el selector
    todas_semanas = Semana.query.order_by(Semana.fecha_inicio.desc()).all()
    
    # Obtener shorts de la semana actual
    shorts = Short.query.filter_by(semana_id=semana_actual.id).order_by(Short.dia_publicacion).all()
    
    # Agrupar por días de la semana
    dias_orden = ['lunes', 'martes', 'miercoles', 'jueves', 'viernes', 'sabado', 'domingo']
    shorts_por_dia = {dia: [] for dia in dias_orden}
    
    for short in shorts:
        if short.dia_nombre in shorts_por_dia:
            shorts_por_dia[short.dia_nombre].append(short)
    
    # Estadísticas de la semana
    total_shorts = len(shorts)
    completados = len([s for s in shorts if s.estado == 'completado'])
    en_proceso = len([s for s in shorts if s.estado in ['en_proceso', 'guion_generado']])
    investigacion = len([s for s in shorts if s.estado == 'investigacion'])
    
    progreso = (completados / semana_actual.videos_objetivo * 100) if semana_actual.videos_objetivo > 0 else 0
    
    stats = {
        'total_shorts': total_shorts,
        'completados': completados,
        'en_proceso': en_proceso,
        'investigacion': investigacion,
        'pendientes': total_shorts - completados - en_proceso,
        'progreso': round(progreso, 1)
    }
    
    return render_template('dashboard.html', 
                         semana_actual=semana_actual,
                         todas_semanas=todas_semanas,
                         shorts_por_dia=shorts_por_dia,
                         dias_orden=dias_orden,
                         stats=stats)

@app.route('/nueva_semana')
@login_required
def nueva_semana():
    # Crear la próxima semana
    ultima_semana = Semana.query.order_by(Semana.fecha_inicio.desc()).first()
    
    if ultima_semana:
        # Crear semana siguiente
        nueva_fecha_inicio = ultima_semana.fecha_fin + timedelta(days=1)
    else:
        # Primera semana
        hoy = datetime.now().date()
        dias_hasta_lunes = hoy.weekday()
        nueva_fecha_inicio = hoy - timedelta(days=dias_hasta_lunes)
    
    nueva_fecha_fin = nueva_fecha_inicio + timedelta(days=6)
    
    # Determinar número de semana del mes
    primer_dia_mes = nueva_fecha_inicio.replace(day=1)
    dias_desde_inicio_mes = (nueva_fecha_inicio - primer_dia_mes).days
    numero_semana = (dias_desde_inicio_mes // 7) + 1
    
    # Nombres de meses en español
    meses = [
        '', 'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
        'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'
    ]
    
    nueva_semana_obj = Semana(
        numero_semana=numero_semana,
        mes=meses[nueva_fecha_inicio.month],
        año=nueva_fecha_inicio.year,
        fecha_inicio=nueva_fecha_inicio,
        fecha_fin=nueva_fecha_fin,
        estado='planificacion',
        videos_objetivo=21  # 3 shorts por día x 7 días
    )
    
    db.session.add(nueva_semana_obj)
    db.session.commit()
    
    flash(f'Nueva semana creada: Semana {numero_semana} - {meses[nueva_fecha_inicio.month]} {nueva_fecha_inicio.year}', 'success')
    return redirect(url_for('dashboard', semana_id=nueva_semana_obj.id))

def crear_semana_actual():
    """Crear la semana actual automáticamente"""
    hoy = datetime.now().date()
    
    # Encontrar el lunes de esta semana
    dias_hasta_lunes = hoy.weekday()  # 0 = lunes, 6 = domingo
    lunes = hoy - timedelta(days=dias_hasta_lunes)
    domingo = lunes + timedelta(days=6)
    
    # Determinar número de semana del mes
    primer_dia_mes = hoy.replace(day=1)
    dias_desde_inicio_mes = (lunes - primer_dia_mes).days
    numero_semana = (dias_desde_inicio_mes // 7) + 1
    
    # Nombres de meses en español
    meses = [
        '', 'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
        'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'
    ]
    
    nueva_semana = Semana(
        numero_semana=numero_semana,
        mes=meses[hoy.month],
        año=hoy.year,
        fecha_inicio=lunes,
        fecha_fin=domingo,
        estado='activa',
        videos_objetivo=21  # 3 shorts por día x 7 días
    )
    
    db.session.add(nueva_semana)
    db.session.commit()
    
    return nueva_semana

def migrar_datos_antiguos():
    """Migrar datos del formato anterior al nuevo sistema semanal"""
    try:
        # Verificar si hay columna semana_id (nuevos modelos)
        inspector = db.inspect(db.engine)
        if 'short' not in inspector.get_table_names():
            return
            
        columns = [col['name'] for col in inspector.get_columns('short')]
        if 'semana_id' not in columns:
            return
            
        # Verificar si existen shorts antiguos sin semana_id
        shorts_antiguos = Short.query.filter(Short.semana_id == None).all()
        
        if not shorts_antiguos:
            return
            
        # Crear semana de migración para datos antiguos
        semana_migracion = Semana.query.filter_by(estado='migracion').first()
        
        if not semana_migracion:
            hoy = datetime.now().date()
            dias_hasta_lunes = hoy.weekday()
            lunes = hoy - timedelta(days=dias_hasta_lunes)
            domingo = lunes + timedelta(days=6)
            
            semana_migracion = Semana(
                numero_semana=1,
                mes='Migración',
                año=2025,
                fecha_inicio=lunes,
                fecha_fin=domingo,
                estado='migracion',
                videos_objetivo=len(shorts_antiguos)
            )
            db.session.add(semana_migracion)
            db.session.commit()
        
        # Migrar cada short antiguo
        dias_map = {
            'lunes': 0, 'martes': 1, 'miercoles': 2, 'jueves': 3, 
            'viernes': 4, 'sabado': 5, 'domingo': 6
        }
        
        for short in shorts_antiguos:
            dia_offset = dias_map.get(getattr(short, 'dia', 'lunes'), 0)
            fecha_publicacion = semana_migracion.fecha_inicio + timedelta(days=dia_offset)
            
            # Actualizar short con nuevos campos
            short.semana_id = semana_migracion.id
            short.dia_publicacion = fecha_publicacion
            short.dia_nombre = getattr(short, 'dia', 'lunes')
            short.orden_dia = 1
            
        db.session.commit()
        print(f"✅ Migrados {len(shorts_antiguos)} shorts al nuevo sistema semanal")
        
    except Exception as e:
        print(f"⚠️ Error en migración: {str(e)}")

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

@app.route('/buscar_videos')
@login_required
def buscar_videos():
    return render_template('buscar_videos.html')

@app.route('/api/buscar_videos_youtube', methods=['POST'])
@login_required
def buscar_videos_youtube():
    data = request.json
    nicho = data.get('nicho', 'finanzas')
    duracion = data.get('duracion', 'long')
    dias = data.get('dias', 7)
    
    # Configurar consulta de búsqueda por nicho
    nichos_queries = {
        'finanzas': 'financial advice money investing wealth',
        'emprendimiento': 'entrepreneur business startup success',
        'negocios': 'business strategy marketing sales',
        'liderazgo': 'leadership management CEO motivation',
        'tecnologia': 'technology innovation AI startup tech'
    }
    
    query = nichos_queries.get(nicho, 'success motivation')
    
    try:
        # Configurar parámetros de búsqueda
        published_after = (datetime.utcnow() - timedelta(days=dias)).isoformat() + 'Z'
        
        search_params = {
            'part': 'snippet',
            'q': query,
            'type': 'video',
            'videoDuration': duracion,
            'publishedAfter': published_after,
            'order': 'viewCount',
            'maxResults': 20,
            'key': app.config['YOUTUBE_API_KEY']
        }
        
        # Buscar videos
        search_response = requests.get(
            'https://www.googleapis.com/youtube/v3/search',
            params=search_params
        )
        
        if search_response.status_code != 200:
            return jsonify({'error': 'Error en YouTube API'}), 500
            
        search_data = search_response.json()
        video_ids = [item['id']['videoId'] for item in search_data['items']]
        
        # Obtener estadísticas detalladas
        stats_params = {
            'part': 'statistics,contentDetails',
            'id': ','.join(video_ids),
            'key': app.config['YOUTUBE_API_KEY']
        }
        
        stats_response = requests.get(
            'https://www.googleapis.com/youtube/v3/videos',
            params=stats_params
        )
        
        if stats_response.status_code != 200:
            return jsonify({'error': 'Error obteniendo estadísticas'}), 500
            
        stats_data = stats_response.json()
        
        # Procesar y calcular VPH
        videos_procesados = []
        
        for i, item in enumerate(search_data['items']):
            if i < len(stats_data['items']):
                video_stats = stats_data['items'][i]
                
                # Calcular VPH
                view_count = int(video_stats['statistics'].get('viewCount', 0))
                published_at = datetime.fromisoformat(item['snippet']['publishedAt'].replace('Z', '+00:00'))
                hours_since_published = (datetime.now(published_at.tzinfo) - published_at).total_seconds() / 3600
                vph = round(view_count / hours_since_published if hours_since_published > 0 else 0)
                
                # Filtrar solo videos con buen VPH
                if vph >= 100:  # Mínimo 100 VPH
                    videos_procesados.append({
                        'video_id': item['id']['videoId'],
                        'titulo': item['snippet']['title'],
                        'canal': item['snippet']['channelTitle'],
                        'publicado': item['snippet']['publishedAt'],
                        'views': view_count,
                        'vph': vph,
                        'duracion': video_stats['contentDetails']['duration'],
                        'url': f"https://www.youtube.com/watch?v={item['id']['videoId']}",
                        'thumbnail': item['snippet']['thumbnails']['medium']['url']
                    })
        
        # Ordenar por VPH descendente
        videos_procesados.sort(key=lambda x: x['vph'], reverse=True)
        
        return jsonify({
            'success': True,
            'videos': videos_procesados[:10],  # Top 10
            'total_encontrados': len(videos_procesados)
        })
        
    except Exception as e:
        return jsonify({'error': f'Error: {str(e)}'}), 500

@app.route('/api/analizar_video', methods=['POST'])
@login_required  
def analizar_video():
    data = request.json
    video_url = data.get('video_url')
    
    if not video_url:
        return jsonify({'error': 'URL requerida'}), 400
        
    try:
        # Usar OpenAI o Claude para análisis
        if app.config['OPENAI_API_KEY']:
            client = openai.OpenAI(api_key=app.config['OPENAI_API_KEY'])
            
            prompt = f"""
            Analiza este video de YouTube para crear shorts virales: {video_url}
            
            Proporciona:
            1. 3 momentos más virales (timestamp aproximado)
            2. Títulos optimizados para cada momento
            3. Hooks de apertura
            4. Overlays sugeridos
            5. Análisis de por qué sería viral
            
            Formato: JSON
            """
            
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1000
            )
            
            analisis = response.choices[0].message.content
            
        elif anthropic_client:
            prompt = f"""
            Analiza este video de YouTube: {video_url}
            
            Identifica los 3 momentos más virales y crea:
            1. Timestamps específicos
            2. Títulos para shorts
            3. Hooks de apertura
            4. Overlays de texto
            5. Razón de viralidad
            
            Responde en JSON estructurado.
            """
            
            message = anthropic_client.messages.create(
                model="claude-3-sonnet-20240229",
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}]
            )
            
            analisis = message.content[0].text
            
        else:
            return jsonify({'error': 'No hay APIs de IA configuradas'}), 400
            
        return jsonify({
            'success': True,
            'analisis': analisis,
            'video_url': video_url
        })
        
    except Exception as e:
        return jsonify({'error': f'Error en análisis: {str(e)}'}), 500

@app.route('/api/generar_guion', methods=['POST'])
@login_required
def generar_guion():
    data = request.json
    video_url = data.get('video_url')
    titulo = data.get('titulo', '')
    nicho = data.get('nicho', 'finanzas')
    
    if not video_url:
        return jsonify({'error': 'URL requerida'}), 400
        
    try:
        # Template base según el nicho
        templates_nicho = {
            'finanzas': {
                'hook': '🚨 ERROR Financiero que ARRUINA tu futuro',
                'estructura': '[0-5s] Hook impactante → [5-45s] Clip + 3 overlays → [45-60s] Tu análisis + CTA',
                'overlays': ['❌ "ERROR: No consideran esto"', '⚠️ "RIESGO: Para ciertos casos"', '✅ "MEJOR: Alternativa real"'],
                'cta': 'Sígueme para más consejos que cambiarán tu vida financiera'
            },
            'emprendimiento': {
                'hook': '🚀 SECRETO de Emprendedor que cambió TODO',
                'estructura': '[0-5s] Hook viral → [5-45s] Historia + insights → [45-60s] Lección práctica',
                'overlays': ['💡 "INSIGHT: Esto es clave"', '📈 "RESULTADO: Impacto real"', '🎯 "APLICA: Tu siguiente paso"'],
                'cta': 'Sígueme para estrategias de emprendimiento real'
            }
        }
        
        template = templates_nicho.get(nicho, templates_nicho['finanzas'])
        
        # Usar IA para personalizar el guión
        if app.config['OPENAI_API_KEY']:
            client = openai.OpenAI(api_key=app.config['OPENAI_API_KEY'])
            
            prompt = f"""
            Crea un guión detallado para un short viral de 60 segundos basado en:
            
            VIDEO FUENTE: {video_url}
            TÍTULO SUGERIDO: {titulo}
            NICHO: {nicho}
            
            ESTRUCTURA REQUERIDA:
            {template['estructura']}
            
            OVERLAYS BASE:
            {', '.join(template['overlays'])}
            
            Proporciona:
            1. Hook específico (5 segundos)
            2. Guión palabra por palabra con timestamps
            3. 3 overlays personalizados con timing exacto
            4. Conclusión viral
            5. CTA optimizado
            
            Formato: JSON estructurado con timestamps precisos
            """
            
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1200
            )
            
            guion_generado = response.choices[0].message.content
            
        elif anthropic_client:
            prompt = f"""
            Genera un guión completo para short viral basado en: {video_url}
            
            Título: {titulo}
            Nicho: {nicho}
            Duración: 60 segundos
            
            Estructura:
            - Hook impactante (0-5s)
            - Contenido principal con clip (5-45s) 
            - Conclusión + CTA (45-60s)
            
            Incluye 3 overlays de texto con timing específico.
            
            Respuesta en JSON con timestamps exactos.
            """
            
            message = anthropic_client.messages.create(
                model="claude-3-sonnet-20240229",
                max_tokens=1200,
                messages=[{"role": "user", "content": prompt}]
            )
            
            guion_generado = message.content[0].text
            
        else:
            # Guión template básico sin IA
            guion_generado = {
                'hook': template['hook'],
                'estructura': template['estructura'],
                'overlays': template['overlays'],
                'cta': template['cta'],
                'video_fuente': video_url
            }
            
        return jsonify({
            'success': True,
            'guion': guion_generado,
            'titulo_sugerido': titulo,
            'nicho': nicho
        })
        
    except Exception as e:
        return jsonify({'error': f'Error generando guión: {str(e)}'}), 500

@app.route('/generador_guiones')
@login_required 
def generador_guiones():
    return render_template('generador_guiones.html')

@app.route('/analisis_canales')
@login_required
def analisis_canales():
    return render_template('analisis_canales.html')

@app.route('/api/analizar_canal', methods=['POST'])
@login_required
def analizar_canal():
    data = request.json
    canal_url = data.get('canal_url', '')
    
    if not canal_url:
        return jsonify({'error': 'URL del canal requerida'}), 400
        
    try:
        # Extraer channel ID de la URL
        channel_id = None
        if 'channel/' in canal_url:
            channel_id = canal_url.split('channel/')[-1].split('/')[0]
        elif '@' in canal_url:
            # Handle @username format - need to search for channel
            username = canal_url.split('@')[-1].split('/')[0]
            search_params = {
                'part': 'snippet',
                'q': username,
                'type': 'channel',
                'maxResults': 1,
                'key': app.config['YOUTUBE_API_KEY']
            }
            
            search_response = requests.get(
                'https://www.googleapis.com/youtube/v3/search',
                params=search_params
            )
            
            if search_response.status_code == 200:
                search_data = search_response.json()
                if search_data['items']:
                    channel_id = search_data['items'][0]['id']['channelId']
        
        if not channel_id:
            return jsonify({'error': 'No se pudo extraer el ID del canal'}), 400
            
        # Obtener información del canal
        channel_params = {
            'part': 'snippet,statistics',
            'id': channel_id,
            'key': app.config['YOUTUBE_API_KEY']
        }
        
        channel_response = requests.get(
            'https://www.googleapis.com/youtube/v3/channels',
            params=channel_params
        )
        
        if channel_response.status_code != 200:
            return jsonify({'error': 'Error obteniendo información del canal'}), 500
            
        channel_data = channel_response.json()
        if not channel_data['items']:
            return jsonify({'error': 'Canal no encontrado'}), 404
            
        channel_info = channel_data['items'][0]
        
        # Obtener videos recientes del canal
        videos_params = {
            'part': 'snippet',
            'channelId': channel_id,
            'order': 'date',
            'maxResults': 20,
            'key': app.config['YOUTUBE_API_KEY']
        }
        
        videos_response = requests.get(
            'https://www.googleapis.com/youtube/v3/search',
            params=videos_params
        )
        
        if videos_response.status_code != 200:
            return jsonify({'error': 'Error obteniendo videos del canal'}), 500
            
        videos_data = videos_response.json()
        video_ids = [item['id']['videoId'] for item in videos_data['items'] if 'videoId' in item['id']]
        
        # Obtener estadísticas de los videos
        stats_params = {
            'part': 'statistics,contentDetails',
            'id': ','.join(video_ids[:10]),  # Limitar a 10 videos
            'key': app.config['YOUTUBE_API_KEY']
        }
        
        stats_response = requests.get(
            'https://www.googleapis.com/youtube/v3/videos',
            params=stats_params
        )
        
        if stats_response.status_code != 200:
            return jsonify({'error': 'Error obteniendo estadísticas'}), 500
            
        stats_data = stats_response.json()
        
        # Procesar y analizar videos
        videos_analisis = []
        total_views = 0
        
        for i, item in enumerate(videos_data['items'][:10]):
            if i < len(stats_data['items']) and 'videoId' in item['id']:
                video_stats = stats_data['items'][i]
                
                view_count = int(video_stats['statistics'].get('viewCount', 0))
                like_count = int(video_stats['statistics'].get('likeCount', 0))
                comment_count = int(video_stats['statistics'].get('commentCount', 0))
                
                # Calcular VPH
                published_at = datetime.fromisoformat(item['snippet']['publishedAt'].replace('Z', '+00:00'))
                hours_since_published = max((datetime.now(published_at.tzinfo) - published_at).total_seconds() / 3600, 1)
                vph = round(view_count / hours_since_published)
                
                # Calcular engagement
                engagement_rate = round(((like_count + comment_count) / view_count * 100) if view_count > 0 else 0, 2)
                
                videos_analisis.append({
                    'titulo': item['snippet']['title'],
                    'video_id': item['id']['videoId'],
                    'publicado': item['snippet']['publishedAt'],
                    'views': view_count,
                    'likes': like_count,
                    'comentarios': comment_count,
                    'vph': vph,
                    'engagement': engagement_rate,
                    'duracion': video_stats['contentDetails']['duration'],
                    'thumbnail': item['snippet']['thumbnails']['medium']['url']
                })
                
                total_views += view_count
        
        # Calcular estadísticas del canal
        promedio_views = round(total_views / len(videos_analisis)) if videos_analisis else 0
        promedio_vph = round(sum(v['vph'] for v in videos_analisis) / len(videos_analisis)) if videos_analisis else 0
        promedio_engagement = round(sum(v['engagement'] for v in videos_analisis) / len(videos_analisis), 2) if videos_analisis else 0
        
        # Identificar videos más virales (top 3)
        videos_virales = sorted(videos_analisis, key=lambda x: x['vph'], reverse=True)[:3]
        
        resultado = {
            'canal_info': {
                'nombre': channel_info['snippet']['title'],
                'descripcion': channel_info['snippet']['description'][:200] + '...' if len(channel_info['snippet']['description']) > 200 else channel_info['snippet']['description'],
                'suscriptores': channel_info['statistics'].get('subscriberCount', 'N/A'),
                'total_videos': channel_info['statistics'].get('videoCount', 'N/A'),
                'total_views': channel_info['statistics'].get('viewCount', 'N/A'),
                'thumbnail': channel_info['snippet']['thumbnails']['medium']['url']
            },
            'estadisticas': {
                'promedio_views': promedio_views,
                'promedio_vph': promedio_vph,
                'promedio_engagement': promedio_engagement,
                'videos_analizados': len(videos_analisis)
            },
            'videos_virales': videos_virales,
            'todos_videos': videos_analisis
        }
        
        return jsonify({
            'success': True,
            'resultado': resultado
        })
        
    except Exception as e:
        return jsonify({'error': f'Error: {str(e)}'}), 500

@app.route('/planificar_semana')
@login_required
def planificar_semana():
    semana_id = request.args.get('semana_id')
    if not semana_id:
        flash('ID de semana requerido', 'error')
        return redirect(url_for('dashboard'))
    
    semana = Semana.query.get_or_404(semana_id)
    return render_template('planificar_semana.html', semana=semana)

@app.route('/api/planificar_semana_automatica', methods=['POST'])
@login_required
def planificar_semana_automatica():
    data = request.json
    semana_id = data.get('semana_id')
    nicho_principal = data.get('nicho', 'finanzas')
    
    if not semana_id:
        return jsonify({'error': 'ID de semana requerido'}), 400
        
    semana = Semana.query.get_or_404(semana_id)
    
    try:
        # Configurar búsqueda por nicho
        nichos_queries = {
            'finanzas': 'financial advice money investing wealth',
            'emprendimiento': 'entrepreneur business startup success',
            'negocios': 'business strategy marketing sales',
            'liderazgo': 'leadership management CEO motivation',
            'tecnologia': 'technology innovation AI startup tech'
        }
        
        query = nichos_queries.get(nicho_principal, 'success motivation')
        
        # Buscar videos virales para toda la semana
        search_params = {
            'part': 'snippet',
            'q': query,
            'type': 'video',
            'videoDuration': 'long',
            'publishedAfter': (datetime.utcnow() - timedelta(days=7)).isoformat() + 'Z',
            'order': 'viewCount',
            'maxResults': 50,  # Buscar más videos para tener opciones
            'key': app.config['YOUTUBE_API_KEY']
        }
        
        search_response = requests.get(
            'https://www.googleapis.com/youtube/v3/search',
            params=search_params
        )
        
        if search_response.status_code != 200:
            return jsonify({'error': 'Error en YouTube API'}), 500
            
        search_data = search_response.json()
        video_ids = [item['id']['videoId'] for item in search_data['items']]
        
        # Obtener estadísticas detalladas
        stats_params = {
            'part': 'statistics,contentDetails',
            'id': ','.join(video_ids),
            'key': app.config['YOUTUBE_API_KEY']
        }
        
        stats_response = requests.get(
            'https://www.googleapis.com/youtube/v3/videos',
            params=stats_params
        )
        
        if stats_response.status_code != 200:
            return jsonify({'error': 'Error obteniendo estadísticas'}), 500
            
        stats_data = stats_response.json()
        
        # Procesar videos y calcular VPH
        videos_candidatos = []
        
        for i, item in enumerate(search_data['items']):
            if i < len(stats_data['items']):
                video_stats = stats_data['items'][i]
                
                view_count = int(video_stats['statistics'].get('viewCount', 0))
                published_at = datetime.fromisoformat(item['snippet']['publishedAt'].replace('Z', '+00:00'))
                hours_since_published = (datetime.now(published_at.tzinfo) - published_at).total_seconds() / 3600
                vph = round(view_count / hours_since_published if hours_since_published > 0 else 0)
                
                # Filtrar solo videos con buen VPH
                if vph >= 100:
                    videos_candidatos.append({
                        'video_id': item['id']['videoId'],
                        'titulo': item['snippet']['title'],
                        'canal': item['snippet']['channelTitle'],
                        'publicado': item['snippet']['publishedAt'],
                        'views': view_count,
                        'vph': vph,
                        'duracion': video_stats['contentDetails']['duration'],
                        'url': f"https://www.youtube.com/watch?v={item['id']['videoId']}",
                        'thumbnail': item['snippet']['thumbnails']['medium']['url']
                    })
        
        # Ordenar por VPH y tomar los mejores
        videos_candidatos.sort(key=lambda x: x['vph'], reverse=True)
        videos_seleccionados = videos_candidatos[:21]  # 21 videos para la semana
        
        if len(videos_seleccionados) < 21:
            return jsonify({'error': f'Solo se encontraron {len(videos_seleccionados)} videos, se necesitan 21'}), 400
        
        # Crear shorts para cada día
        dias_orden = ['lunes', 'martes', 'miercoles', 'jueves', 'viernes', 'sabado', 'domingo']
        shorts_creados = 0
        
        for dia_idx, dia in enumerate(dias_orden):
            fecha_publicacion = semana.fecha_inicio + timedelta(days=dia_idx)
            
            # Crear 3 shorts para este día
            for orden in range(1, 4):
                video_idx = dia_idx * 3 + (orden - 1)
                if video_idx < len(videos_seleccionados):
                    video = videos_seleccionados[video_idx]
                    
                    # Verificar si ya existe un short para este día y orden
                    short_existente = Short.query.filter_by(
                        semana_id=semana.id,
                        dia_nombre=dia,
                        orden_dia=orden
                    ).first()
                    
                    if not short_existente:
                        nuevo_short = Short(
                            semana_id=semana.id,
                            dia_publicacion=fecha_publicacion,
                            dia_nombre=dia,
                            orden_dia=orden,
                            titulo=video['titulo'],
                            tema=nicho_principal,
                            estado='investigacion',
                            video_fuente_url=video['url'],
                            video_fuente_id=video['video_id'],
                            vph_fuente=video['vph']
                        )
                        db.session.add(nuevo_short)
                        shorts_creados += 1
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'shorts_creados': shorts_creados,
            'videos_encontrados': len(videos_candidatos),
            'mensaje': f'Semana planificada con {shorts_creados} shorts basados en videos virales'
        })
        
    except Exception as e:
        return jsonify({'error': f'Error: {str(e)}'}), 500

@app.route('/planificar_dia')
@login_required
def planificar_dia():
    dia = request.args.get('dia')
    fecha = request.args.get('fecha')
    semana_id = request.args.get('semana_id')
    
    if not all([dia, fecha, semana_id]):
        flash('Parámetros requeridos faltantes', 'error')
        return redirect(url_for('dashboard'))
    
    return render_template('planificar_dia.html', dia=dia, fecha=fecha, semana_id=semana_id)

@app.route('/generar_guiones_masivo')
@login_required
def generar_guiones_masivo():
    semana_id = request.args.get('semana_id')
    if not semana_id:
        flash('ID de semana requerido', 'error')
        return redirect(url_for('dashboard'))
    
    semana = Semana.query.get_or_404(semana_id)
    return render_template('generar_guiones_masivo.html', semana=semana)

@app.route('/api/generar_guiones_semana', methods=['POST'])
@login_required
def generar_guiones_semana():
    data = request.json
    semana_id = data.get('semana_id')
    
    if not semana_id:
        return jsonify({'error': 'ID de semana requerido'}), 400
        
    semana = Semana.query.get_or_404(semana_id)
    
    try:
        # Obtener shorts sin guión generado
        shorts_pendientes = Short.query.filter(
            Short.semana_id == semana.id,
            Short.estado == 'investigacion'
        ).all()
        
        if not shorts_pendientes:
            return jsonify({'error': 'No hay shorts pendientes de generar guiones'}), 400
        
        guiones_generados = 0
        errores = []
        
        for short in shorts_pendientes:
            try:
                # Templates base según el tema
                templates_tema = {
                    'finanzas': {
                        'hook': f'🚨 ERROR Financiero que ARRUINA tu futuro',
                        'estructura': '[0-5s] Hook impactante → [5-45s] Clip + 3 overlays → [45-60s] Tu análisis + CTA',
                        'overlays': ['❌ "ERROR: No consideran esto"', '⚠️ "RIESGO: Para ciertos casos"', '✅ "MEJOR: Alternativa real"'],
                        'cta': 'Sígueme para más consejos que cambiarán tu vida financiera'
                    },
                    'emprendimiento': {
                        'hook': f'🚀 SECRETO de Emprendedor que cambió TODO',
                        'estructura': '[0-5s] Hook viral → [5-45s] Historia + insights → [45-60s] Lección práctica',
                        'overlays': ['💡 "INSIGHT: Esto es clave"', '📈 "RESULTADO: Impacto real"', '🎯 "APLICA: Tu siguiente paso"'],
                        'cta': 'Sígueme para estrategias de emprendimiento real'
                    }
                }
                
                template = templates_tema.get(short.tema, templates_tema['finanzas'])
                
                if app.config['OPENAI_API_KEY']:
                    client = openai.OpenAI(api_key=app.config['OPENAI_API_KEY'])
                    
                    prompt = f"""
                    Crea un guión detallado para un short viral de 60 segundos:
                    
                    VIDEO FUENTE: {short.video_fuente_url}
                    TÍTULO: {short.titulo}
                    TEMA: {short.tema}
                    VPH: {short.vph_fuente}
                    
                    ESTRUCTURA REQUERIDA:
                    {template['estructura']}
                    
                    FORMATO REQUERIDO:
                    1. Hook específico (5 segundos)
                    2. Guión palabra por palabra con timestamps
                    3. 3 overlays personalizados con timing exacto
                    4. Conclusión viral
                    5. CTA optimizado
                    
                    Genera un JSON estructurado con timestamps precisos.
                    """
                    
                    response = client.chat.completions.create(
                        model="gpt-3.5-turbo",
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=1200
                    )
                    
                    guion_generado = response.choices[0].message.content
                    
                elif anthropic_client:
                    prompt = f"""
                    Genera un guión completo para short viral:
                    
                    Video: {short.video_fuente_url}
                    Título: {short.titulo}
                    Tema: {short.tema}
                    Duración: 60 segundos
                    
                    Incluye:
                    - Hook impactante (0-5s)
                    - Contenido principal (5-45s) 
                    - Conclusión + CTA (45-60s)
                    - 3 overlays con timing específico
                    
                    Respuesta en formato JSON estructurado.
                    """
                    
                    message = anthropic_client.messages.create(
                        model="claude-3-sonnet-20240229",
                        max_tokens=1200,
                        messages=[{"role": "user", "content": prompt}]
                    )
                    
                    guion_generado = message.content[0].text
                    
                else:
                    # Template básico sin IA
                    guion_generado = json.dumps({
                        'hook': template['hook'],
                        'estructura': template['estructura'],
                        'overlays': template['overlays'],
                        'cta': template['cta'],
                        'video_fuente': short.video_fuente_url,
                        'titulo': short.titulo,
                        'tema': short.tema
                    })
                
                # Actualizar short con guión generado
                short.guion_generado = guion_generado
                short.estado = 'guion_generado'
                guiones_generados += 1
                
            except Exception as e:
                errores.append(f'Error en {short.dia_nombre} #{short.orden_dia}: {str(e)}')
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'guiones_generados': guiones_generados,
            'errores': errores,
            'mensaje': f'Generados {guiones_generados} guiones. {len(errores)} errores.'
        })
        
    except Exception as e:
        return jsonify({'error': f'Error: {str(e)}'}), 500

@app.route('/api/descargar_videos_masivo', methods=['POST'])
@login_required
def descargar_videos_masivo():
    data = request.json
    semana_id = data.get('semana_id')
    
    if not semana_id:
        return jsonify({'error': 'ID de semana requerido'}), 400
        
    try:
        # Obtener shorts con guión pero sin video descargado
        shorts_pendientes = Short.query.filter(
            Short.semana_id == semana_id,
            Short.video_descargado == False,
            Short.video_fuente_id != None
        ).all()
        
        if not shorts_pendientes:
            return jsonify({'error': 'No hay videos pendientes de descargar'}), 400
        
        videos_descargados = 0
        
        # Simular descarga (en producción usarías yt-dlp o similar)
        for short in shorts_pendientes:
            try:
                # Aquí implementarías la descarga real con yt-dlp
                # Por ahora solo marcamos como descargado
                short.video_descargado = True
                videos_descargados += 1
                
                # Opcional: actualizar estado a en_proceso si ya tiene guión
                if short.guion_generado:
                    short.estado = 'en_proceso'
                    
            except Exception as e:
                continue
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'videos_descargados': videos_descargados,
            'mensaje': f'{videos_descargados} videos marcados como descargados'
        })
        
    except Exception as e:
        return jsonify({'error': f'Error: {str(e)}'}), 500

@app.route('/gestionar_pendientes')
@login_required
def gestionar_pendientes():
    semana_id = request.args.get('semana_id')
    if not semana_id:
        flash('ID de semana requerido', 'error')
        return redirect(url_for('dashboard'))
    
    semana = Semana.query.get_or_404(semana_id)
    return render_template('gestionar_pendientes.html', semana=semana)

@app.route('/api/analizar_semana_pendientes', methods=['POST'])
@login_required
def analizar_semana_pendientes():
    data = request.json
    semana_id = data.get('semana_id')
    
    if not semana_id:
        return jsonify({'error': 'ID de semana requerido'}), 400
        
    semana = Semana.query.get_or_404(semana_id)
    
    try:
        # Obtener todos los shorts de la semana
        todos_shorts = Short.query.filter_by(semana_id=semana.id).all()
        
        # Clasificar por estado
        completados = [s for s in todos_shorts if s.estado == 'completado']
        en_proceso = [s for s in todos_shorts if s.estado == 'en_proceso']
        con_guion = [s for s in todos_shorts if s.estado == 'guion_generado']
        investigacion = [s for s in todos_shorts if s.estado == 'investigacion']
        
        # Identificar problemas por día
        dias_orden = ['lunes', 'martes', 'miercoles', 'jueves', 'viernes', 'sabado', 'domingo']
        analisis_por_dia = {}
        
        for dia in dias_orden:
            shorts_dia = [s for s in todos_shorts if s.dia_nombre == dia]
            
            analisis_por_dia[dia] = {
                'total': len(shorts_dia),
                'completados': len([s for s in shorts_dia if s.estado == 'completado']),
                'pendientes': len([s for s in shorts_dia if s.estado != 'completado']),
                'shorts': [{
                    'id': s.id,
                    'orden': s.orden_dia,
                    'titulo': s.titulo,
                    'estado': s.estado,
                    'vph_fuente': s.vph_fuente,
                    'dias_desde_creacion': (datetime.now().date() - s.dia_publicacion).days if s.dia_publicacion else 0
                } for s in shorts_dia]
            }
        
        # Calcular estadísticas generales
        total_shorts = len(todos_shorts)
        tasa_completado = (len(completados) / total_shorts * 100) if total_shorts > 0 else 0
        
        # Identificar shorts críticos (más de 3 días sin avance)
        shorts_criticos = []
        for short in todos_shorts:
            if short.estado != 'completado' and short.dia_publicacion:
                dias_retraso = (datetime.now().date() - short.dia_publicacion).days
                if dias_retraso > 3:
                    shorts_criticos.append({
                        'id': short.id,
                        'titulo': short.titulo,
                        'dia': short.dia_nombre,
                        'orden': short.orden_dia,
                        'estado': short.estado,
                        'dias_retraso': dias_retraso,
                        'vph_fuente': short.vph_fuente
                    })
        
        return jsonify({
            'success': True,
            'estadisticas': {
                'total_shorts': total_shorts,
                'completados': len(completados),
                'en_proceso': len(en_proceso),
                'con_guion': len(con_guion),
                'investigacion': len(investigacion),
                'tasa_completado': round(tasa_completado, 1)
            },
            'analisis_por_dia': analisis_por_dia,
            'shorts_criticos': sorted(shorts_criticos, key=lambda x: x['dias_retraso'], reverse=True),
            'recomendaciones': generar_recomendaciones(semana, shorts_criticos, tasa_completado)
        })
        
    except Exception as e:
        return jsonify({'error': f'Error: {str(e)}'}), 500

@app.route('/api/reasignar_shorts', methods=['POST'])
@login_required
def reasignar_shorts():
    data = request.json
    accion = data.get('accion')  # 'mover_semana', 'cancelar', 'priorizar'
    shorts_ids = data.get('shorts_ids', [])
    semana_destino_id = data.get('semana_destino_id')
    
    if not accion or not shorts_ids:
        return jsonify({'error': 'Acción y shorts requeridos'}), 400
    
    try:
        shorts_afectados = Short.query.filter(Short.id.in_(shorts_ids)).all()
        resultado = {'success': True, 'procesados': 0, 'errores': []}
        
        for short in shorts_afectados:
            try:
                if accion == 'mover_semana' and semana_destino_id:
                    semana_destino = Semana.query.get(semana_destino_id)
                    if semana_destino:
                        # Encontrar el próximo slot disponible en la nueva semana
                        dias_orden = ['lunes', 'martes', 'miercoles', 'jueves', 'viernes', 'sabado', 'domingo']
                        slot_encontrado = False
                        
                        for dia_idx, dia in enumerate(dias_orden):
                            for orden in range(1, 4):  # 3 slots por día
                                slot_ocupado = Short.query.filter_by(
                                    semana_id=semana_destino.id,
                                    dia_nombre=dia,
                                    orden_dia=orden
                                ).first()
                                
                                if not slot_ocupado:
                                    # Mover short a este slot
                                    short.semana_id = semana_destino.id
                                    short.dia_nombre = dia
                                    short.orden_dia = orden
                                    short.dia_publicacion = semana_destino.fecha_inicio + timedelta(days=dia_idx)
                                    short.estado = 'investigacion'  # Reiniciar estado
                                    slot_encontrado = True
                                    break
                            if slot_encontrado:
                                break
                        
                        if not slot_encontrado:
                            resultado['errores'].append(f'No hay slots disponibles para {short.titulo}')
                            continue
                            
                elif accion == 'cancelar':
                    short.estado = 'cancelado'
                    short.notas = (short.notas or '') + f'\n[{datetime.now().strftime("%d/%m/%Y")}] Cancelado por gestión de pendientes'
                    
                elif accion == 'priorizar':
                    # Marcar como prioritario y mover al inicio de la cola
                    if short.estado == 'investigacion':
                        short.estado = 'guion_generado'  # Saltar a siguiente etapa
                    short.notas = (short.notas or '') + f'\n[{datetime.now().strftime("%d/%m/%Y")}] Priorizado'
                
                resultado['procesados'] += 1
                
            except Exception as e:
                resultado['errores'].append(f'Error con {short.titulo}: {str(e)}')
        
        db.session.commit()
        
        return jsonify(resultado)
        
    except Exception as e:
        return jsonify({'error': f'Error: {str(e)}'}), 500

@app.route('/api/generar_videos_backup', methods=['POST'])
@login_required
def generar_videos_backup():
    data = request.json
    semana_id = data.get('semana_id')
    cantidad = data.get('cantidad', 5)  # Videos backup por defecto
    nicho = data.get('nicho', 'finanzas')
    
    if not semana_id:
        return jsonify({'error': 'ID de semana requerido'}), 400
        
    try:
        semana = Semana.query.get_or_404(semana_id)
        
        # Buscar videos adicionales con diferentes criterios
        nichos_queries = {
            'finanzas': 'financial mistakes money advice investing tips',
            'emprendimiento': 'startup entrepreneur business failure success',
            'negocios': 'business marketing strategy leadership',
            'liderazgo': 'leadership CEO management skills',
            'tecnologia': 'technology AI innovation startup tech'
        }
        
        query = nichos_queries.get(nicho, 'success motivation')
        
        # Buscar videos con criterios más amplios para backups
        search_params = {
            'part': 'snippet',
            'q': query,
            'type': 'video',
            'videoDuration': 'medium',  # Videos medios para más opciones
            'publishedAfter': (datetime.utcnow() - timedelta(days=14)).isoformat() + 'Z',  # 2 semanas
            'order': 'relevance',  # Cambiar criterio
            'maxResults': cantidad * 2,  # Buscar más para filtrar
            'key': app.config['YOUTUBE_API_KEY']
        }
        
        search_response = requests.get(
            'https://www.googleapis.com/youtube/v3/search',
            params=search_params
        )
        
        if search_response.status_code != 200:
            return jsonify({'error': 'Error en YouTube API'}), 500
            
        search_data = search_response.json()
        video_ids = [item['id']['videoId'] for item in search_data['items']]
        
        # Obtener estadísticas
        stats_params = {
            'part': 'statistics,contentDetails',
            'id': ','.join(video_ids),
            'key': app.config['YOUTUBE_API_KEY']
        }
        
        stats_response = requests.get(
            'https://www.googleapis.com/youtube/v3/videos',
            params=stats_params
        )
        
        if stats_response.status_code != 200:
            return jsonify({'error': 'Error obteniendo estadísticas'}), 500
            
        stats_data = stats_response.json()
        
        # Procesar y filtrar videos backup
        videos_backup = []
        
        for i, item in enumerate(search_data['items']):
            if i < len(stats_data['items']) and len(videos_backup) < cantidad:
                video_stats = stats_data['items'][i]
                
                view_count = int(video_stats['statistics'].get('viewCount', 0))
                published_at = datetime.fromisoformat(item['snippet']['publishedAt'].replace('Z', '+00:00'))
                hours_since_published = (datetime.now(published_at.tzinfo) - published_at).total_seconds() / 3600
                vph = round(view_count / hours_since_published if hours_since_published > 0 else 0)
                
                # Criterios más flexibles para backups
                if vph >= 50:  # Menor VPH para backups
                    videos_backup.append({
                        'video_id': item['id']['videoId'],
                        'titulo': item['snippet']['title'],
                        'canal': item['snippet']['channelTitle'],
                        'views': view_count,
                        'vph': vph,
                        'url': f"https://www.youtube.com/watch?v={item['id']['videoId']}",
                        'thumbnail': item['snippet']['thumbnails']['medium']['url'],
                        'tipo': 'backup'
                    })
        
        return jsonify({
            'success': True,
            'videos_backup': videos_backup,
            'cantidad_encontrados': len(videos_backup),
            'mensaje': f'Encontrados {len(videos_backup)} videos backup como alternativas'
        })
        
    except Exception as e:
        return jsonify({'error': f'Error: {str(e)}'}), 500

def generar_recomendaciones(semana, shorts_criticos, tasa_completado):
    recomendaciones = []
    
    if tasa_completado < 50:
        recomendaciones.append({
            'tipo': 'critico',
            'titulo': 'Tasa de completado muy baja',
            'descripcion': 'Menos del 50% de shorts completados. Considera reasignar videos a la próxima semana.',
            'accion': 'reasignar_masivo'
        })
    
    if len(shorts_criticos) > 5:
        recomendaciones.append({
            'tipo': 'warning',
            'titulo': 'Muchos videos en retraso',
            'descripcion': f'{len(shorts_criticos)} videos con más de 3 días de retraso.',
            'accion': 'priorizar_criticos'
        })
    
    if tasa_completado > 80:
        recomendaciones.append({
            'tipo': 'success',
            'titulo': 'Excelente progreso',
            'descripcion': 'Más del 80% completado. Considera generar videos backup para próximas semanas.',
            'accion': 'generar_backup'
        })
    
    # Recomendar según día de la semana
    hoy = datetime.now().weekday()  # 0 = lunes
    if hoy >= 4:  # Viernes o después
        recomendaciones.append({
            'tipo': 'info',
            'titulo': 'Revisión semanal recomendada',
            'descripcion': 'Es un buen momento para revisar pendientes y planificar la próxima semana.',
            'accion': 'revisar_semanal'
        })
    
    return recomendaciones

def init_db():
    with app.app_context():
        db.create_all()
        
        # Migrar datos antiguos si existen
        migrar_datos_antiguos()
        
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

def create_app():
    init_db()
    return app

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
else:
    # Para Railway/Gunicorn
    init_db()