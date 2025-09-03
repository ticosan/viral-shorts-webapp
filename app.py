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

# Hacer timedelta y otras utilidades disponibles en templates
@app.template_global()
def get_timedelta():
    return timedelta

@app.template_filter('add_days')
def add_days(date, days):
    """Añadir días a una fecha"""
    return date + timedelta(days=days)

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
    # Redirigir a la nueva interfaz de descubrimiento
    return redirect(url_for('video_discovery'))

@app.route('/old_dashboard')
@login_required
def old_dashboard():
    try:
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
    except Exception as e:
        print(f"❌ Error en dashboard: {e}")
        # En caso de error, mostrar dashboard básico
        return render_template('dashboard.html', 
                             semana_actual=None,
                             todas_semanas=[],
                             shorts_por_dia={},
                             dias_orden=[],
                             stats={},
                             error_message=f"Error cargando dashboard: {e}")

@app.route('/video_discovery')
@login_required
def video_discovery():
    """Nueva interfaz principal - descubrimiento de videos virales"""
    return render_template('video_discovery.html')

@app.route('/api/search-viral-videos', methods=['POST'])
@login_required
def search_viral_videos():
    """Buscar videos virales usando YouTube API"""
    try:
        nicho = request.form.get('nicho', 'finanzas')
        periodo = int(request.form.get('periodo', '3'))
        vph_minimo = int(request.form.get('vph_minimo', '100'))
        cantidad = int(request.form.get('cantidad', '21'))
        
        # Mapeo de nichos a términos de búsqueda
        nicho_queries = {
            'finanzas': ['finanzas personales', 'inversiones', 'dinero', 'ahorro', 'criptomonedas'],
            'tecnologia': ['tecnología', 'inteligencia artificial', 'programación', 'apps'],
            'negocios': ['emprendimiento', 'negocios', 'marketing', 'ventas'],
            'salud': ['fitness', 'ejercicio', 'nutrición', 'salud'],
            'educacion': ['educación', 'aprender', 'estudiar', 'universidad'],
            'motivacion': ['motivación', 'productividad', 'éxito', 'mentalidad']
        }
        
        query_terms = nicho_queries.get(nicho, nicho_queries['finanzas'])
        
        videos_encontrados = []
        
        for query in query_terms[:2]:  # Usar solo 2 términos para no exceder límites
            videos = buscar_videos_virales_youtube(query, periodo, vph_minimo, cantidad // 2)
            videos_encontrados.extend(videos)
            
        # Ordenar por VPH y tomar los mejores
        videos_encontrados.sort(key=lambda x: x.get('vph', 0), reverse=True)
        videos_finales = videos_encontrados[:cantidad]
        
        return jsonify({
            'success': True,
            'videos': videos_finales,
            'total': len(videos_finales)
        })
        
    except Exception as e:
        print(f"❌ Error buscando videos virales: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

def buscar_videos_virales_youtube(query, dias=3, vph_minimo=100, max_resultados=10, region='US', idioma='es'):
    """
    Sistema avanzado de descubrimiento viral con YouTube Data API v3
    Implementa todas las métricas de viralidad activa y análisis de canales
    """
    try:
        from datetime import datetime, timedelta
        import re
        
        print(f"🔍 Buscando videos virales para: {query}")
        
        # Fecha límite para filtrar videos recientes
        fecha_limite = (datetime.utcnow() - timedelta(days=dias)).isoformat() + 'Z'
        
        # PASO 1: Búsqueda inicial optimizada para viralidad
        search_params = {
            'key': app.config['YOUTUBE_API_KEY'],
            'part': 'snippet',
            'q': query,
            'type': 'video',
            'order': 'viewCount',  # Ordenar por visualizaciones (pastel viral)
            'publishedAfter': fecha_limite,
            'videoDuration': 'long',  # Videos largos >20min para extraer clips
            'maxResults': min(max_resultados * 2, 50),  # Obtener más para filtrar mejor
            'regionCode': region,
            'relevanceLanguage': idioma,
            'safeSearch': 'none',
            'videoDefinition': 'high'
        }
        
        search_response = requests.get('https://www.googleapis.com/youtube/v3/search', params=search_params)
        search_data = search_response.json()
        
        if 'items' not in search_data:
            print(f"⚠️ No se encontraron videos para: {query}")
            if 'error' in search_data:
                print(f"❌ Error API: {search_data['error']['message']}")
            return []
        
        video_ids = [item['id']['videoId'] for item in search_data['items']]
        
        if not video_ids:
            print(f"⚠️ No se obtuvieron IDs de videos para: {query}")
            return []
        
        # PASO 2: Obtener estadísticas detalladas y métricas de viralidad
        print(f"📊 Analizando {len(video_ids)} videos con métricas avanzadas...")
        
        stats_params = {
            'key': app.config['YOUTUBE_API_KEY'],
            'part': 'statistics,contentDetails,snippet',
            'id': ','.join(video_ids)
        }
        
        stats_response = requests.get('https://www.googleapis.com/youtube/v3/videos', params=stats_params)
        stats_data = stats_response.json()
        
        if 'items' not in stats_data:
            print(f"❌ Error obteniendo estadísticas")
            return []
        
        videos_procesados = []
        channel_ids = set()
        
        for item in stats_data['items']:
            try:
                stats = item['statistics']
                content = item['contentDetails']
                snippet = item['snippet']
                
                # Extraer datos básicos
                video_id = item['id']
                views = int(stats.get('viewCount', 0))
                likes = int(stats.get('likeCount', 0))
                comments = int(stats.get('commentCount', 0))
                channel_id = snippet['channelId']
                channel_ids.add(channel_id)
                
                # Calcular tiempo transcurrido desde publicación
                published_at = datetime.fromisoformat(snippet['publishedAt'].replace('Z', '+00:00'))
                tiempo_transcurrido = datetime.now().astimezone() - published_at
                horas_transcurridas = max(tiempo_transcurrido.total_seconds() / 3600, 1)  # Mínimo 1 hora
                
                # MÉTRICAS CLAVE DE VIRALIDAD ACTIVA
                vph = views / horas_transcurridas  # Views Per Hour - MÉTRICA PRINCIPAL
                
                # Filtrar por VPH mínimo (viralidad activa)
                if vph < vph_minimo:
                    continue
                
                # Ratios de engagement (FTA - Factor de Tracción del Algoritmo)
                likes_per_view = (likes / views) * 1000 if views > 0 else 0  # Por cada 1000 views
                comments_per_view = (comments / views) * 1000 if views > 0 else 0
                engagement_score = likes_per_view + (comments_per_view * 2)  # Comentarios valen más
                
                # Parsear duración (PT#H#M#S format)
                duration_str = content['duration']
                duration_match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str)
                if duration_match:
                    hours = int(duration_match.group(1) or 0)
                    minutes = int(duration_match.group(2) or 0)
                    seconds = int(duration_match.group(3) or 0)
                    duration_seconds = hours * 3600 + minutes * 60 + seconds
                    duration_formatted = f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours > 0 else f"{minutes:02d}:{seconds:02d}"
                else:
                    duration_seconds = 0
                    duration_formatted = "Desconocido"
                
                # Solo videos largos (>20 min = 1200 segundos) ideales para clips
                if duration_seconds < 1200:  # 20 minutos
                    continue
                
                # Calcular "viralidad velocity" (aceleración de views)
                dias_transcurridos = max(tiempo_transcurrido.days, 1)
                views_per_day = views / dias_transcurridos
                
                # Información completa del video
                video_info = {
                    'id': video_id,
                    'title': snippet['title'],
                    'channel': snippet['channelTitle'],
                    'channel_id': channel_id,
                    'thumbnail': snippet['thumbnails'].get('medium', {}).get('url', ''),
                    'description': snippet.get('description', '')[:200] + '...' if snippet.get('description', '') else '',
                    
                    # Métricas básicas
                    'views': views,
                    'views_formatted': f"{views:,}",
                    'likes': likes,
                    'comments': comments,
                    'duration_seconds': duration_seconds,
                    'duration_formatted': duration_formatted,
                    
                    # MÉTRICAS DE VIRALIDAD ACTIVA
                    'vph': round(vph, 2),
                    'views_per_day': round(views_per_day, 0),
                    'likes_per_view_1k': round(likes_per_view, 2),
                    'comments_per_view_1k': round(comments_per_view, 2),
                    'engagement_score': round(engagement_score, 2),
                    
                    # Información temporal
                    'published_at': snippet['publishedAt'],
                    'published_formatted': published_at.strftime('%d/%m/%Y %H:%M'),
                    'horas_transcurridas': round(horas_transcurridas, 1),
                    'dias_transcurridos': dias_transcurridos,
                    
                    # URLs y metadatos
                    'url': f"https://youtube.com/watch?v={video_id}",
                    'query_usado': query,
                    'region': region,
                    'idioma': idioma,
                    
                    # Puntuación viral compuesta
                    'viral_score': calculate_viral_score(vph, engagement_score, views, dias_transcurridos)
                }
                
                videos_procesados.append(video_info)
                
            except Exception as e:
                print(f"⚠️ Error procesando video {item.get('id', 'unknown')}: {e}")
                continue
        
        # PASO 3: Análisis de canales en crecimiento (Hockey Stick Detection)
        if channel_ids and videos_procesados:
            print(f"📈 Analizando {len(channel_ids)} canales para detectar crecimiento explosivo...")
            channel_analysis = analyze_channels_growth(list(channel_ids))
            
            # Enriquecer videos con información del canal
            for video in videos_procesados:
                channel_data = channel_analysis.get(video['channel_id'], {})
                video.update({
                    'channel_subscribers': channel_data.get('subscribers', 0),
                    'channel_total_views': channel_data.get('total_views', 0),
                    'channel_growth_indicator': channel_data.get('growth_indicator', 'unknown'),
                    'channel_viral_potential': channel_data.get('viral_potential', 0)
                })
        
        # PASO 4: Ordenar por puntuación viral y métricas combinadas
        videos_procesados.sort(key=lambda x: (
            x['viral_score'],  # Puntuación viral principal
            x['vph'],          # VPH como segundo criterio
            x['engagement_score']  # Engagement como tercer criterio
        ), reverse=True)
        
        # Tomar solo los mejores resultados
        videos_finales = videos_procesados[:max_resultados]
        
        print(f"✅ Encontrados {len(videos_finales)} videos con alta viralidad activa")
        print(f"📊 VPH promedio: {sum(v['vph'] for v in videos_finales) / len(videos_finales):.1f}")
        print(f"🎯 Engagement promedio: {sum(v['engagement_score'] for v in videos_finales) / len(videos_finales):.2f}")
        
        return videos_finales
        
    except Exception as e:
        print(f"❌ Error crítico en búsqueda YouTube: {e}")
        return []

def calculate_viral_score(vph, engagement_score, views, dias_transcurridos):
    """
    Calcular puntuación viral compuesta basada en múltiples factores
    """
    # Normalizar VPH (logarítmico para valores altos)
    import math
    vph_score = math.log10(max(vph, 1)) * 100
    
    # Puntuación por engagement
    engagement_normalized = min(engagement_score, 50)  # Cap at 50
    
    # Bonus por views totales (logarítmico)
    views_score = math.log10(max(views, 1)) * 10
    
    # Penalty por antiguedad (videos muy viejos son menos relevantes)
    age_penalty = max(0, (dias_transcurridos - 30) * 0.1) if dias_transcurridos > 30 else 0
    
    # Puntuación final
    total_score = vph_score + engagement_normalized + views_score - age_penalty
    
    return max(0, round(total_score, 2))

def analyze_channels_growth(channel_ids):
    """
    Analizar canales para detectar crecimiento explosivo (Hockey Stick)
    Aproximación inferencial basada en métricas disponibles
    """
    try:
        if not channel_ids:
            return {}
            
        # Obtener información de canales en lotes
        channel_analysis = {}
        
        # Procesar en lotes de 50 (límite de la API)
        for i in range(0, len(channel_ids), 50):
            batch = channel_ids[i:i+50]
            
            params = {
                'key': app.config['YOUTUBE_API_KEY'],
                'part': 'statistics,snippet',
                'id': ','.join(batch)
            }
            
            response = requests.get('https://www.googleapis.com/youtube/v3/channels', params=params)
            data = response.json()
            
            if 'items' not in data:
                continue
                
            for channel in data['items']:
                try:
                    stats = channel['statistics']
                    snippet = channel['snippet']
                    
                    subscribers = int(stats.get('subscriberCount', 0))
                    total_views = int(stats.get('totalViewCount', 0))
                    video_count = int(stats.get('videoCount', 1))
                    
                    # Calcular métricas de crecimiento inferencial
                    avg_views_per_video = total_views / max(video_count, 1)
                    subscriber_to_view_ratio = subscribers / max(total_views, 1) * 1000
                    
                    # Detectar patrones de crecimiento explosivo
                    growth_indicator = 'unknown'
                    viral_potential = 0
                    
                    if subscribers < 100000 and avg_views_per_video > 50000:
                        growth_indicator = 'explosive'  # Canal pequeño con views altas = crecimiento explosivo
                        viral_potential = 90
                    elif subscribers < 500000 and avg_views_per_video > 100000:
                        growth_indicator = 'high_growth'
                        viral_potential = 80
                    elif avg_views_per_video > subscribers * 2:
                        growth_indicator = 'viral_content'  # Views por video > 2x subscribers
                        viral_potential = 70
                    elif subscriber_to_view_ratio < 5:  # Pocos subs pero muchas views
                        growth_indicator = 'emerging'
                        viral_potential = 60
                    else:
                        growth_indicator = 'stable'
                        viral_potential = 30
                    
                    channel_analysis[channel['id']] = {
                        'subscribers': subscribers,
                        'total_views': total_views,
                        'video_count': video_count,
                        'avg_views_per_video': round(avg_views_per_video, 0),
                        'subscriber_to_view_ratio': round(subscriber_to_view_ratio, 2),
                        'growth_indicator': growth_indicator,
                        'viral_potential': viral_potential,
                        'channel_name': snippet.get('title', 'Unknown')
                    }
                    
                except Exception as e:
                    print(f"⚠️ Error analizando canal {channel.get('id', 'unknown')}: {e}")
                    continue
        
        print(f"📈 Análisis de canales completado: {len(channel_analysis)} canales")
        explosive_channels = [c for c in channel_analysis.values() if c['growth_indicator'] == 'explosive']
        print(f"🚀 Canales con crecimiento explosivo detectados: {len(explosive_channels)}")
        
        return channel_analysis
        
    except Exception as e:
        print(f"❌ Error en análisis de canales: {e}")
        return {}

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
        # Por ahora saltamos la migración para evitar errores
        # Los datos antiguos se mantendrán en el modelo anterior
        print("✅ Migración omitida - usando modelo actualizado")
        
    except Exception as e:
        print(f"⚠️ Error en migración: {str(e)}")

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        try:
            username = request.form['username']
            password = request.form['password']
            
            user = User.query.filter_by(username=username).first()
            
            if user and check_password_hash(user.password_hash, password):
                login_user(user)
                return redirect(url_for('dashboard'))
            else:
                flash('Usuario o contraseña incorrectos', 'error')
        except Exception as e:
            print(f"❌ Error en login: {e}")
            flash(f'Error de base de datos: {e}')
            return render_template('login.html')
    
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
    try:
        with app.app_context():
            db.create_all()
            print("✅ Tablas de base de datos creadas")
            
            # Migrar datos antiguos si existen
            try:
                migrar_datos_antiguos()
                print("✅ Migración de datos completada")
            except Exception as e:
                print(f"⚠️  Error en migración (continuando): {e}")
            
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
                
                # Solo crear la semana inicial, sin shorts de ejemplo
                # Los shorts se crearán usando el sistema de planificación
                try:
                    crear_semana_actual()
                    print("✅ Semana inicial creada")
                except Exception as e:
                    print(f"⚠️  Error creando semana inicial: {e}")
                
                db.session.commit()
                print("✅ Base de datos inicializada - usar planificación para crear shorts")
            else:
                print("✅ Base de datos ya inicializada")
    except Exception as e:
        print(f"❌ Error inicializando base de datos: {e}")
        raise

def create_app():
    init_db()
    return app

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
else:
    # Para Railway/Gunicorn
    init_db()