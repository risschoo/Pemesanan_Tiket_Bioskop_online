from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, abort
import mysql.connector
import os
import uuid
import hashlib
import hmac
from datetime import datetime, timedelta, date
from functools import wraps
from werkzeug.utils import secure_filename
import midtransclient
import requests
import base64
import json

import config_loader 

def _cfg(key, default=None):
    return os.environ.get(key, default)

app = Flask(__name__)
app.secret_key = _cfg('FLASK_SECRET_KEY', 'cgvbioskop2024_fallback_dev_only')

UPLOAD_FOLDER_FARIS = os.path.join('static', 'uploads')
ALLOWED_EXTENSIONS_FARIS = {'png', 'jpg', 'jpeg'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER_FARIS

if not os.path.exists(UPLOAD_FOLDER_FARIS):
    os.makedirs(UPLOAD_FOLDER_FARIS)

#server key midtrans
MIDTRANS_SERVER_KEY   = _cfg('MIDTRANS_SERVER_KEY',   'Mid-server-gMZxTVNEDZE4fbHnaz9qYv9T')
MIDTRANS_CLIENT_KEY   = _cfg('MIDTRANS_CLIENT_KEY',   'Mid-client-PvwImZjz6-_b08sv')
MIDTRANS_MERCHANT_ID  = _cfg('MIDTRANS_MERCHANT_ID',  'M670785059')
MIDTRANS_IS_PRODUCTION = _cfg('MIDTRANS_IS_PRODUCTION', 'False').lower() == 'true'

#Batas Waktu Pembayaran
PAYMENT_EXPIRE_MINUTES = int(_cfg('PAYMENT_EXPIRE_MINUTES', 15))

# Coming Soon
WINDOW_TAMPIL_HARI = 4

snap = midtransclient.Snap(
    is_production=MIDTRANS_IS_PRODUCTION,
    server_key=MIDTRANS_SERVER_KEY,
    client_key=MIDTRANS_CLIENT_KEY
)

core = midtransclient.CoreApi(
    is_production=MIDTRANS_IS_PRODUCTION,
    server_key=MIDTRANS_SERVER_KEY,
    client_key=MIDTRANS_CLIENT_KEY
)

#Validasi Hak akses

def login_required(f):
    """Pastikan user sudah login (role apapun)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id_faris' not in session:
            flash('Silakan login terlebih dahulu.', 'warning')
            return redirect(url_for('login_faris'))
        return f(*args, **kwargs)
    return decorated

def role_required(*roles):
    """Pastikan user memiliki role yang diizinkan. Contoh: @role_required('admin', 'pengelola')"""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'user_id_faris' not in session:
                flash('Silakan login terlebih dahulu.', 'warning')
                return redirect(url_for('login_faris'))
            if session.get('role_faris') not in roles:
                flash('Anda tidak memiliki akses ke halaman ini.', 'danger')
                # Kembalikan ke dashboard sesuai role
                role = session.get('role_faris')
                if role == 'admin':
                    return redirect(url_for('admin_dashboard_faris'))
                elif role == 'pengelola':
                    return redirect(url_for('pengelola_dashboard_faris'))
                else:
                    return redirect(url_for('index_faris'))
            return f(*args, **kwargs)
        return decorated
    return decorator

def pengguna_required(f):
    """Shortcut untuk role pengguna saja."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id_faris' not in session:
            flash('Silakan login sebagai pengguna dulu!', 'warning')
            return redirect(url_for('login_faris'))
        if session.get('role_faris') != 'pengguna':
            flash('Halaman ini hanya untuk pengguna biasa.', 'danger')
            return redirect(url_for('index_faris'))
        return f(*args, **kwargs)
    return decorated

# Fungsi Bantuan
def get_db_faris():
    return mysql.connector.connect(
        host='localhost',
        user='root',
        password='',
        database='cgv_bioskop_faris'
    )

def allowed_file_faris(filename_faris):
    return '.' in filename_faris and filename_faris.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS_FARIS

#buat ngerapihin format jam tayang di template
def format_jam_faris(td_faris):
    if td_faris is None:
        return '-'
    if isinstance(td_faris, timedelta):
        total_detik_faris = int(td_faris.total_seconds())
        jam_faris = total_detik_faris // 3600
        menit_faris = (total_detik_faris % 3600) // 60
        return f"{jam_faris:02d}:{menit_faris:02d}"
    return td_faris.strftime('%H:%M')

app.jinja_env.filters['format_jam_faris'] = format_jam_faris

def cek_bentrok_jadwal(id_teater, tanggal, jam_mulai, durasi, id_jadwal_ignore=None):
    db = get_db_faris()
    cur = db.cursor(dictionary=True)
    
    sql = "SELECT j.*, f.durasi_faris FROM jadwal_faris j JOIN film_faris f ON j.id_film_faris = f.id_film_faris WHERE id_teater_faris = %s AND tanggal_faris = %s"
    params = [id_teater, tanggal]
    
    if id_jadwal_ignore:
        sql += " AND id_jadwal_faris != %s"
        params.append(id_jadwal_ignore)
        
    cur.execute(sql, params)
    jadwals = cur.fetchall()
    db.close()

    format_jam = "%H:%M"
    mulai_baru = datetime.strptime(str(jam_mulai), format_jam)
    selesai_baru = mulai_baru + timedelta(minutes=int(durasi) + 10)

    for j in jadwals:
        jam_db = (datetime.min + j['jam_tayang_faris']).time().strftime(format_jam)
        mulai_lama = datetime.strptime(jam_db, format_jam)
        selesai_lama = mulai_lama + timedelta(minutes=int(j['durasi_faris']) + 10)

        if mulai_baru < selesai_lama and selesai_baru > mulai_lama:
            return True, f"Bentrok dengan jadwal jam {jam_db}"
            
    return False, ""

def update_status_jadwal(id_jadwal_faris):
    db_faris = get_db_faris()
    cur_faris = db_faris.cursor(dictionary=True)
    cur_faris.execute("""
        SELECT t.kapasitas_faris 
        FROM jadwal_faris j
        JOIN teater_faris t ON j.id_teater_faris = t.id_teater_faris
        WHERE j.id_jadwal_faris = %s
    """, (id_jadwal_faris,))
    result = cur_faris.fetchone()
    kapasitas = result['kapasitas_faris'] if result else 0
    cur_faris.execute("""
        SELECT COUNT(DISTINCT dp.id_kursi_faris) as terisi
        FROM detail_pemesanan_faris dp
        JOIN pemesanan_faris p ON dp.id_pemesanan_faris = p.id_pemesanan_faris
        WHERE p.id_jadwal_faris = %s AND p.status_bayar_faris = 'lunas'
    """, (id_jadwal_faris,))
    terisi = cur_faris.fetchone()['terisi'] or 0
    status_baru = 'penuh' if terisi >= kapasitas else 'tersedia'
    cur_faris.execute("UPDATE jadwal_faris SET status_faris = %s WHERE id_jadwal_faris = %s", 
                      (status_baru, id_jadwal_faris))
    db_faris.commit()
    db_faris.close()

#Lepas Kursi yang Expired

def expire_pending_payments():
    """
    Cari semua transaksi dengan status 'pending' yang sudah melewati
    batas waktu PAYMENT_EXPIRE_MINUTES. Ubah statusnya menjadi 'expired'
    agar kursi bisa dipesan kembali oleh pengguna lain.
    Fungsi ini dipanggil otomatis setiap kali halaman pilih-kursi dibuka.
    """
    try:
        db = get_db_faris()
        cur = db.cursor()
        batas_waktu = datetime.now() - timedelta(minutes=PAYMENT_EXPIRE_MINUTES)
        # Ambil daftar id_jadwal yang terdampak agar status-nya bisa diupdate
        cur.execute("""
            SELECT id_pemesanan_faris, id_jadwal_faris
            FROM pemesanan_faris
            WHERE status_bayar_faris IN ('pending', 'belum')
              AND created_at_faris < %s
        """, (batas_waktu,))
        expired_list = cur.fetchall()

        if expired_list:
            cur.execute("""
                UPDATE pemesanan_faris
                SET status_bayar_faris = 'expired'
                WHERE status_bayar_faris IN ('pending', 'belum')
                  AND created_at_faris < %s
            """, (batas_waktu,))
            db.commit()
            # Update status jadwal yang terdampak
            jadwal_terdampak = set(row[1] for row in expired_list)
            for id_jadwal in jadwal_terdampak:
                update_status_jadwal(id_jadwal)
        db.close()
    except Exception as e:
        print(f"[expire_pending_payments] Error: {e}")

def create_midtrans_transaction(id_pemesanan_faris, total_harga, nama_user, email_user, no_hp_user, kursi_list):
    order_id = f"CGV-{id_pemesanan_faris}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    item_details = [{
        'id': str(id_pemesanan_faris),
        'price': int(total_harga),
        'quantity': 1,
        'name': f"Pemesanan Tiket CGV ({len(kursi_list)} kursi)"
    }]
    param = {
        "transaction_details": {
            "order_id": order_id,
            "gross_amount": int(total_harga)
        },
        "customer_details": {
            "first_name": (nama_user or "Customer")[:50],
            "email": email_user or "customer@example.com",
            "phone": no_hp_user or "08123456789"
        },
        "item_details": item_details,
        "credit_card": {"secure": True},
        "enabled_payments": ["credit_card", "bank_transfer", "gopay", "qris", "shopeepay", "other_qris"]
    }
    try:
        print(f"Creating transaction for order: {order_id}")
        response = snap.create_transaction(param)
        print(f"Midtrans response: {response}")
        if 'token' in response:
            if 'order_id' not in response:
                response['order_id'] = order_id
            return response
        return None
    except Exception as e:
        print(f"Midtrans Error: {str(e)}")
        return None

# Halaman Index atau route index
@app.route('/')
def index_faris():
    keyword_faris = request.args.get('q_faris', '').strip()
    genre_filter  = request.args.get('genre', '').strip()
    studio_filter = request.args.get('studio', '').strip()
    film_filter   = request.args.get('film', '').strip()

    db_faris = get_db_faris()
    cur_faris = db_faris.cursor(dictionary=True)

    # Film hanya tampil ke pelanggan jika punya jadwal dalam window WINDOW_TAMPIL_HARI hari ke depan.
    # Jadwal yang lebih dari 4 hari ke depan disembunyikan sampai waktunya tiba.
    hari_ini_idx = datetime.now().date()
    batas_tampil = hari_ini_idx + timedelta(days=WINDOW_TAMPIL_HARI)

    where_parts = [
        "f.status_faris = 'tayang'",
        "j.tanggal_faris BETWEEN %(hari_ini)s AND %(batas_tampil)s"
    ]
    params = {'hari_ini': hari_ini_idx, 'batas_tampil': batas_tampil}

    if keyword_faris:
        where_parts.append("(f.judul_faris LIKE %(kw)s OR f.genre_faris LIKE %(kw)s)")
        params['kw'] = f'%{keyword_faris}%'

    if genre_filter:
        where_parts.append("f.genre_faris LIKE %(genre)s")
        params['genre'] = f'%{genre_filter}%'

    if studio_filter:
        where_parts.append("j.id_teater_faris = %(studio)s")
        params['studio'] = studio_filter

    if film_filter:
        where_parts.append("f.id_film_faris = %(film_id)s")
        params['film_id'] = film_filter

    where_sql = "WHERE " + " AND ".join(where_parts)

    cur_faris.execute(f"""
        SELECT DISTINCT f.*
        FROM film_faris f
        JOIN jadwal_faris j ON f.id_film_faris = j.id_film_faris
        {where_sql}
        ORDER BY f.id_film_faris DESC
    """, params)
    films_faris = cur_faris.fetchall()

    # Genre & studio hanya dari film dalam window tampil
    cur_faris.execute("""
        SELECT DISTINCT f.genre_faris
        FROM film_faris f
        JOIN jadwal_faris j ON f.id_film_faris = j.id_film_faris
        WHERE f.status_faris = 'tayang'
          AND j.tanggal_faris BETWEEN %s AND %s
        ORDER BY f.genre_faris
    """, (hari_ini_idx, batas_tampil))
    genres_faris = [r['genre_faris'] for r in cur_faris.fetchall() if r['genre_faris']]

    cur_faris.execute("""
        SELECT DISTINCT t.id_teater_faris, t.nama_teater_faris
        FROM teater_faris t
        JOIN jadwal_faris j ON t.id_teater_faris = j.id_teater_faris
        WHERE j.tanggal_faris BETWEEN %s AND %s
        ORDER BY t.nama_teater_faris
    """, (hari_ini_idx, batas_tampil))
    studios_faris = cur_faris.fetchall()

    cs_where_parts = []
    cs_params_list = []

    cs_keyword_clause = ""
    cs_genre_clause = ""
    cs_studio_clause = ""
    cs_film_clause = ""
    cs_extra_params = []

    if keyword_faris:
        cs_keyword_clause = "AND (f.judul_faris LIKE %s OR f.genre_faris LIKE %s)"
        cs_extra_params += [f'%{keyword_faris}%', f'%{keyword_faris}%']
    if genre_filter:
        cs_genre_clause = "AND f.genre_faris LIKE %s"
        cs_extra_params.append(f'%{genre_filter}%')
    if film_filter:
        cs_film_clause = "AND f.id_film_faris = %s"
        cs_extra_params.append(film_filter)

    cs_studio_join = ""
    cs_studio_where = ""
    if studio_filter:
        cs_studio_join = "JOIN jadwal_faris jsf ON f.id_film_faris = jsf.id_film_faris"
        cs_studio_where = "AND jsf.id_teater_faris = %s AND jsf.tanggal_faris > %s"
        cs_extra_params_studio = [studio_filter, batas_tampil]
    else:
        cs_extra_params_studio = []

    cur_faris.execute(f"""
        SELECT DISTINCT f.*, MIN(j2.tanggal_faris) AS tanggal_rilis_faris
        FROM film_faris f
        LEFT JOIN jadwal_faris j2 ON f.id_film_faris = j2.id_film_faris
                                  AND j2.tanggal_faris > %s
        {cs_studio_join}
        WHERE (
            f.status_faris = 'coming_soon'
            OR (
                f.status_faris = 'tayang'
                AND EXISTS (
                    SELECT 1 FROM jadwal_faris jcs
                    WHERE jcs.id_film_faris = f.id_film_faris
                      AND jcs.tanggal_faris > %s
                )
                AND NOT EXISTS (
                    SELECT 1 FROM jadwal_faris jnow
                    WHERE jnow.id_film_faris = f.id_film_faris
                      AND jnow.tanggal_faris BETWEEN %s AND %s
                )
            )
        )
        {cs_keyword_clause}
        {cs_genre_clause}
        {cs_film_clause}
        {cs_studio_where}
        GROUP BY f.id_film_faris
        ORDER BY tanggal_rilis_faris ASC, f.id_film_faris DESC
    """, [batas_tampil, batas_tampil, hari_ini_idx, batas_tampil] + cs_extra_params + cs_extra_params_studio)
    coming_soon_films = cur_faris.fetchall()

    # ── ALL TAYANG FILMS for film dropdown (no filter) ─────────────────────────
    cur_faris.execute("""
        SELECT DISTINCT f.id_film_faris, f.judul_faris
        FROM film_faris f
        JOIN jadwal_faris j ON f.id_film_faris = j.id_film_faris
        WHERE f.status_faris = 'tayang'
          AND j.tanggal_faris BETWEEN %s AND %s
        ORDER BY f.judul_faris
    """, (hari_ini_idx, batas_tampil))
    film_list_faris = cur_faris.fetchall()

    db_faris.close()
    return render_template('index_faris.html',
                           films_faris=films_faris,
                           keyword_faris=keyword_faris,
                           genre_filter=genre_filter,
                           studio_filter=studio_filter,
                           film_filter=film_filter,
                           genres_faris=genres_faris,
                           studios_faris=studios_faris,
                           coming_soon_films=coming_soon_films,
                           film_list_faris=film_list_faris)

@app.route('/register_faris', methods=['GET', 'POST'])
def register_faris():
    if request.method == 'POST':
        nama_faris = request.form['nama_faris']
        email_faris = request.form['email_faris']
        password_faris = request.form['password_faris']
        konfirmasi_password_faris = request.form['konfirmasi_password_faris']
        no_hp_faris = request.form['no_hp_faris']

        if password_faris != konfirmasi_password_faris:
            flash('Password dan konfirmasi password tidak cocok!', 'danger')
            return render_template('register_faris.html')

        db_faris = get_db_faris()
        cur_faris = db_faris.cursor()
        try:
            cur_faris.execute(
                "INSERT INTO user_faris (nama_faris, email_faris, password_faris, no_hp_faris, role_faris) VALUES (%s,%s,%s,%s,'pengguna')",
                (nama_faris, email_faris, password_faris, no_hp_faris)
            )
            db_faris.commit()
            flash('Registrasi berhasil! Silakan login.', 'success')
            return redirect(url_for('login_faris'))
        except Exception:
            flash('Email sudah terdaftar!', 'danger')
        finally:
            db_faris.close()
    return render_template('register_faris.html')

@app.route('/login_faris', methods=['GET', 'POST'])
def login_faris():
    if request.method == 'POST':
        email_faris = request.form['email_faris']
        password_faris = request.form['password_faris']
        db_faris = get_db_faris()
        cur_faris = db_faris.cursor(dictionary=True)
        cur_faris.execute("SELECT * FROM user_faris WHERE email_faris=%s AND password_faris=%s", (email_faris, password_faris))
        user_faris = cur_faris.fetchone()
        db_faris.close()
        if user_faris:
            session['user_id_faris'] = user_faris['id_user_faris']
            session['nama_faris'] = user_faris['nama_faris']
            session['role_faris'] = user_faris['role_faris']
            if user_faris['role_faris'] == 'admin':
                return redirect(url_for('admin_dashboard_faris'))
            elif user_faris['role_faris'] == 'pengelola':
                return redirect(url_for('pengelola_dashboard_faris'))
            else:
                return redirect(url_for('index_faris'))
        else:
            flash('Email atau password salah!', 'danger')
    return render_template('login_faris.html')

@app.route('/logout_faris')
def logout_faris():
    session.clear()
    return redirect(url_for('index_faris'))

@app.route('/film_faris/<int:id_film_faris>')
def detail_film_faris(id_film_faris):
    db_faris = get_db_faris()
    cur_faris = db_faris.cursor(dictionary=True)
    cur_faris.execute("SELECT * FROM film_faris WHERE id_film_faris=%s", (id_film_faris,))
    film_faris = cur_faris.fetchone()

    if not film_faris:
        flash('Film tidak ditemukan!', 'danger')
        db_faris.close()
        return redirect(url_for('index_faris'))

    hari_ini_faris = datetime.now().date()
    batas_faris    = hari_ini_faris + timedelta(days=WINDOW_TAMPIL_HARI)

    cur_faris.execute("""
        SELECT COUNT(*) AS ada
        FROM jadwal_faris
        WHERE id_film_faris = %s
          AND tanggal_faris BETWEEN %s AND %s
    """, (id_film_faris, hari_ini_faris, batas_faris))
    ada_dalam_window = cur_faris.fetchone()['ada']

    if ada_dalam_window == 0:
        # Cek apakah film punya jadwal tapi belum waktunya tampil
        cur_faris.execute("""
            SELECT MIN(tanggal_faris) AS tgl_perdana
            FROM jadwal_faris
            WHERE id_film_faris = %s AND tanggal_faris > %s
        """, (id_film_faris, hari_ini_faris))
        row = cur_faris.fetchone()
        db_faris.close()
        if row and row['tgl_perdana']:
            tgl_str = row['tgl_perdana'].strftime('%d %B %Y')
            flash(f'Film ini akan segera tayang mulai {tgl_str}. Nantikan!', 'info')
        else:
            flash('Film ini belum tersedia atau sudah selesai tayang.', 'info')
        return redirect(url_for('index_faris'))

    # Hanya tampilkan jadwal dalam window 4 hari ke depan
    cur_faris.execute("""
        SELECT j.*, t.nama_teater_faris
        FROM jadwal_faris j
        JOIN teater_faris t ON j.id_teater_faris = t.id_teater_faris
        WHERE j.id_film_faris = %s
          AND j.tanggal_faris BETWEEN %s AND %s
          AND j.status_faris = 'tersedia'
        ORDER BY j.tanggal_faris, j.jam_tayang_faris
    """, (id_film_faris, hari_ini_faris, batas_faris))

    jadwals_faris = cur_faris.fetchall()
    db_faris.close()

    return render_template('detail_film_faris.html',
                           film_faris=film_faris,
                           jadwals_faris=jadwals_faris)

@app.route('/pilih_kursi_faris/<int:id_jadwal_faris>')
@pengguna_required
def pilih_kursi_faris(id_jadwal_faris):
    # Bersihkan transaksi expired sebelum tampilkan kursi
    expire_pending_payments()

    db_faris = get_db_faris()
    cur_faris = db_faris.cursor(dictionary=True)
    
    cur_faris.execute("""
        SELECT j.*, f.judul_faris, f.poster_faris, t.nama_teater_faris
        FROM jadwal_faris j
        JOIN film_faris f ON j.id_film_faris = f.id_film_faris
        JOIN teater_faris t ON j.id_teater_faris = t.id_teater_faris
        WHERE j.id_jadwal_faris = %s
    """, (id_jadwal_faris,))
    jadwal_faris = cur_faris.fetchone()
    
    if not jadwal_faris:
        flash('Jadwal tidak ditemukan!', 'danger')
        return redirect(url_for('index_faris'))
    
    if jadwal_faris['status_faris'] == 'penuh':
        flash('Maaf, jadwal ini sudah penuh. Silakan pilih jadwal lain.', 'danger')
        return redirect(url_for('detail_film_faris', id_film_faris=jadwal_faris['id_film_faris']))

    cur_faris.execute("""
        SELECT * FROM kursi_faris WHERE id_teater_faris=%s
        ORDER BY LEFT(kode_kursi_faris,1), CAST(SUBSTRING(kode_kursi_faris, 2) AS UNSIGNED)
    """, (jadwal_faris['id_teater_faris'],))
    semua_kursi_faris = cur_faris.fetchall()

    # Kursi terkunci = lunas ATAU pending yang belum expired
    batas_pending = datetime.now() - timedelta(minutes=PAYMENT_EXPIRE_MINUTES)
    cur_faris.execute("""
        SELECT DISTINCT dp.id_kursi_faris
        FROM detail_pemesanan_faris dp
        JOIN pemesanan_faris p ON dp.id_pemesanan_faris = p.id_pemesanan_faris
        WHERE p.id_jadwal_faris = %s
          AND (
              p.status_bayar_faris = 'lunas'
              OR (p.status_bayar_faris IN ('pending','belum') AND p.created_at_faris >= %s)
          )
    """, (id_jadwal_faris, batas_pending))
    kursi_dipesan_faris = [r['id_kursi_faris'] for r in cur_faris.fetchall()]

    tanggal_faris = jadwal_faris['tanggal_faris']
    hari_faris = tanggal_faris.weekday()
    if hari_faris in [5, 6]:
        harga_faris = jadwal_faris['harga_weekend_faris']
        tipe_hari_faris = 'Weekend'
    else:
        harga_faris = jadwal_faris['harga_weekday_faris']
        tipe_hari_faris = 'Weekday'

    total_kolom_faris = 0
    if semua_kursi_faris:
        for k in semua_kursi_faris:
            try:
                nomor_kolom = int(k['kode_kursi_faris'][1:])
                if nomor_kolom > total_kolom_faris:
                    total_kolom_faris = nomor_kolom
            except ValueError:
                continue

    db_faris.close()
    return render_template('pilih_kursi_faris.html',
                           jadwal_faris=jadwal_faris,
                           semua_kursi_faris=semua_kursi_faris,
                           kursi_dipesan_faris=kursi_dipesan_faris,
                           harga_faris=harga_faris,
                           tipe_hari_faris=tipe_hari_faris,
                           total_kolom_faris=total_kolom_faris,
                           payment_expire_minutes=PAYMENT_EXPIRE_MINUTES)

@app.route('/pesan_faris', methods=['POST'])
@pengguna_required
def pesan_faris():
    id_jadwal_faris = request.form['id_jadwal_faris']
    kursi_dipilih_faris = request.form.getlist('kursi_faris')

    if not kursi_dipilih_faris:
        flash('Pilih minimal 1 kursi!', 'warning')
        return redirect(url_for('pilih_kursi_faris', id_jadwal_faris=id_jadwal_faris))

    db_faris = get_db_faris()
    cur_faris = db_faris.cursor(dictionary=True)

    try:
        # cegah double booking dengan kunci baris jadwal yg dipilih
        db_faris.start_transaction(isolation_level='SERIALIZABLE')

        cur_faris.execute("""
            SELECT j.*, f.judul_faris
            FROM jadwal_faris j
            JOIN film_faris f ON j.id_film_faris = f.id_film_faris
            WHERE j.id_jadwal_faris = %s
            FOR UPDATE
        """, (id_jadwal_faris,))
        jadwal_faris = cur_faris.fetchone()

        if not jadwal_faris:
            db_faris.rollback()
            flash('Jadwal tidak ditemukan!', 'danger')
            return redirect(url_for('index_faris'))

        # Cek ulang kursi di-DB saat ini (real-time)
        batas_pending = datetime.now() - timedelta(minutes=PAYMENT_EXPIRE_MINUTES)
        placeholders = ','.join(['%s'] * len(kursi_dipilih_faris))
        cur_faris.execute(f"""
            SELECT dp.id_kursi_faris, k.kode_kursi_faris
            FROM detail_pemesanan_faris dp
            JOIN pemesanan_faris p ON dp.id_pemesanan_faris = p.id_pemesanan_faris
            JOIN kursi_faris k ON dp.id_kursi_faris = k.id_kursi_faris
            WHERE p.id_jadwal_faris = %s
              AND dp.id_kursi_faris IN ({placeholders})
              AND (
                  p.status_bayar_faris = 'lunas'
                  OR (p.status_bayar_faris IN ('pending','belum') AND p.created_at_faris >= %s)
              )
        """, [id_jadwal_faris] + kursi_dipilih_faris + [batas_pending])
        sudah_diambil = cur_faris.fetchall()

        if sudah_diambil:
            db_faris.rollback()
            kode_bentrok = ', '.join(k['kode_kursi_faris'] for k in sudah_diambil)
            flash(f'⚠️ Maaf! Kursi {kode_bentrok} baru saja dipesan orang lain. Silakan pilih kursi lain.', 'danger')
            return redirect(url_for('pilih_kursi_faris', id_jadwal_faris=id_jadwal_faris))

        # Hitung harga
        cur_faris.execute("SELECT * FROM user_faris WHERE id_user_faris=%s", (session['user_id_faris'],))
        user_faris = cur_faris.fetchone()
        tanggal_faris = jadwal_faris['tanggal_faris']
        harga_faris = jadwal_faris['harga_weekend_faris'] if tanggal_faris.weekday() in [5, 6] else jadwal_faris['harga_weekday_faris']
        total_faris = harga_faris * len(kursi_dipilih_faris)

        cur2_faris = db_faris.cursor()
        cur2_faris.execute(
            "INSERT INTO pemesanan_faris (id_user_faris, id_jadwal_faris, total_harga_faris, status_bayar_faris) VALUES (%s,%s,%s,'belum')",
            (session['user_id_faris'], id_jadwal_faris, total_faris)
        )
        id_pemesanan_faris = cur2_faris.lastrowid

        kursi_data = []
        for id_kursi_faris in kursi_dipilih_faris:
            cur2_faris.execute("INSERT INTO detail_pemesanan_faris (id_pemesanan_faris, id_kursi_faris) VALUES (%s,%s)",
                         (id_pemesanan_faris, id_kursi_faris))
            cur_faris.execute("SELECT kode_kursi_faris FROM kursi_faris WHERE id_kursi_faris=%s", (id_kursi_faris,))
            kursi = cur_faris.fetchone()
            if kursi:
                kursi_data.append(kursi)

        db_faris.commit()

    except mysql.connector.Error as e:
        db_faris.rollback()
        flash('Terjadi kesalahan saat memproses pesanan. Silakan coba lagi.', 'danger')
        print(f"[pesan_faris] DB Error: {e}")
        return redirect(url_for('pilih_kursi_faris', id_jadwal_faris=id_jadwal_faris))
    finally:
        db_faris.close()

    # [POIN 4] Buat transaksi Midtrans
    midtrans_response = create_midtrans_transaction(
        id_pemesanan_faris, total_faris, user_faris['nama_faris'],
        user_faris['email_faris'], user_faris['no_hp_faris'], kursi_data
    )

    if midtrans_response and 'token' in midtrans_response:
        order_id_value = midtrans_response.get('order_id', f"CGV-{id_pemesanan_faris}")
        db2 = get_db_faris()
        cur3 = db2.cursor()
        cur3.execute("UPDATE pemesanan_faris SET snap_token=%s, midtrans_order_id=%s, status_bayar_faris='pending' WHERE id_pemesanan_faris=%s",
                     (midtrans_response['token'], order_id_value, id_pemesanan_faris))
        db2.commit()
        db2.close()
        return render_template('pay_faris.html',
                               snap_token=midtrans_response['token'],
                               midtrans_client_key=MIDTRANS_CLIENT_KEY,
                               id_pemesanan_faris=id_pemesanan_faris,
                               total_harga=total_faris,
                               payment_expire_minutes=PAYMENT_EXPIRE_MINUTES)
    else:
        db2 = get_db_faris()
        cur3 = db2.cursor()
        cur3.execute("DELETE FROM detail_pemesanan_faris WHERE id_pemesanan_faris=%s", (id_pemesanan_faris,))
        cur3.execute("DELETE FROM pemesanan_faris WHERE id_pemesanan_faris=%s", (id_pemesanan_faris,))
        db2.commit()
        db2.close()
        flash('Gagal memproses pembayaran. Silakan coba lagi.', 'danger')
        return redirect(url_for('pilih_kursi_faris', id_jadwal_faris=id_jadwal_faris))

@app.route('/pay_faris/<int:id_pemesanan_faris>')
def pay_faris(id_pemesanan_faris):
    if 'user_id_faris' not in session:
        return redirect(url_for('login_faris'))
    
    db_faris = get_db_faris()
    cur_faris = db_faris.cursor(dictionary=True)
    
    cur_faris.execute("""
        SELECT p.*, u.nama_faris, u.email_faris, u.no_hp_faris
        FROM pemesanan_faris p
        JOIN user_faris u ON p.id_user_faris = u.id_user_faris
        WHERE p.id_pemesanan_faris = %s AND p.id_user_faris = %s
    """, (id_pemesanan_faris, session['user_id_faris']))
    
    pemesanan = cur_faris.fetchone()
    db_faris.close()
    
    if not pemesanan:
        flash('Data pemesanan tidak ditemukan!', 'danger')
        return redirect(url_for('riwayat_faris'))
    
    snap_token = pemesanan.get('snap_token')
    
    if not snap_token:
        flash('Token pembayaran tidak ditemukan. Silakan pesan ulang.', 'danger')
        return redirect(url_for('riwayat_faris'))
    
    return render_template('pay_faris.html',
                          snap_token=snap_token,
                          midtrans_client_key=MIDTRANS_CLIENT_KEY,
                          id_pemesanan_faris=id_pemesanan_faris,
                          total_harga=pemesanan['total_harga_faris'])

# Webhook midtrans
@app.route('/midtrans_notification', methods=['POST'])
def midtrans_notification():
    try:
        notification = request.get_json()
        print("="*50)
        print("WEBHOOK RECEIVED:", notification)
        print("="*50)
        
        order_id = notification.get('order_id')
        transaction_status = notification.get('transaction_status')
        fraud_status = notification.get('fraud_status')
        
        if not order_id:
            return jsonify({'status': 'error', 'message': 'No order_id'}), 400
        
        db_faris = get_db_faris()
        cur_faris = db_faris.cursor(dictionary=True)
        
        cur_faris.execute("SELECT id_pemesanan_faris, id_jadwal_faris FROM pemesanan_faris WHERE midtrans_order_id=%s", (order_id,))
        result = cur_faris.fetchone()
        
        if result:
            id_pemesanan = result['id_pemesanan_faris']
            id_jadwal = result['id_jadwal_faris']
            
            if transaction_status == 'capture':
                if fraud_status == 'accept':
                    status = 'lunas'
                else:
                    status = 'ditolak'
            elif transaction_status == 'settlement':
                status = 'lunas'
            elif transaction_status == 'pending':
                status = 'pending'
            elif transaction_status in ['deny', 'cancel', 'expire']:
                status = 'ditolak'
            else:
                status = 'pending'
            
            cur_faris.execute("UPDATE pemesanan_faris SET status_bayar_faris=%s WHERE id_pemesanan_faris=%s", 
                            (status, id_pemesanan))
            db_faris.commit()
            
            if status == 'lunas':
                update_status_jadwal(id_jadwal)
                print(f"✅ Order {order_id} updated to LUNAS")
        
        db_faris.close()
        return jsonify({'status': 'ok'}), 200
        
    except Exception as e:
        print(f"❌ Webhook error: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/check_status_faris/<int:id_pemesanan_faris>')
def check_status_faris(id_pemesanan_faris):
    if 'user_id_faris' not in session:
        return redirect(url_for('login_faris'))
    
    db_faris = get_db_faris()
    cur_faris = db_faris.cursor(dictionary=True)
    cur_faris.execute("SELECT midtrans_order_id, status_bayar_faris FROM pemesanan_faris WHERE id_pemesanan_faris=%s", 
                     (id_pemesanan_faris,))
    pemesanan = cur_faris.fetchone()
    db_faris.close()
    
    if not pemesanan or not pemesanan['midtrans_order_id']:
        flash('Data pembayaran tidak ditemukan. Silakan pesan ulang.', 'danger')
        return redirect(url_for('riwayat_faris'))
    
    try:
        order_id = pemesanan['midtrans_order_id']
        
        # API endpoint Midtrans Sandbox
        url = f"https://api.sandbox.midtrans.com/v2/{order_id}/status"
        
        # Basic Authentication
        auth_string = f"{MIDTRANS_SERVER_KEY}:"
        encoded_auth = base64.b64encode(auth_string.encode()).decode()
        
        headers = {
            "Accept": "application/json",
            "Authorization": f"Basic {encoded_auth}"
        }
        
        response = requests.get(url, headers=headers)
        data = response.json()
        
        transaction_status = data.get('transaction_status')
        
        print(f"Order ID: {order_id}")
        print(f"Status from Midtrans: {transaction_status}")
        print(f"Full response: {data}")
        
        if transaction_status in ['capture', 'settlement']:
            db_faris = get_db_faris()
            cur_faris = db_faris.cursor()
            cur_faris.execute("UPDATE pemesanan_faris SET status_bayar_faris='lunas' WHERE id_pemesanan_faris=%s", 
                            (id_pemesanan_faris,))
            db_faris.commit()
            
            # Update status jadwal
            cur_faris.execute("SELECT id_jadwal_faris FROM pemesanan_faris WHERE id_pemesanan_faris=%s", (id_pemesanan_faris,))
            result = cur_faris.fetchone()
            if result:
                update_status_jadwal(result[0])
            db_faris.close()
            
            flash('✅ Pembayaran berhasil! Tiket Anda sudah aktif.', 'success')
        elif transaction_status == 'pending':
            flash('⏳ Pembayaran masih pending. Silakan selesaikan pembayaran.', 'warning')
        elif transaction_status in ['deny', 'cancel', 'expire']:
            flash('❌ Pembayaran gagal atau dibatalkan.', 'danger')
        else:
            flash(f'Status: {transaction_status}', 'info')
            
    except Exception as e:
        flash(f'Error cek status: {str(e)}', 'danger')
        print(f"Error details: {str(e)}")
    
    return redirect(url_for('riwayat_faris'))

@app.route('/riwayat_faris')
def riwayat_faris():
    if 'user_id_faris' not in session:
        return redirect(url_for('login_faris'))
    db_faris = get_db_faris()
    cur_faris = db_faris.cursor(dictionary=True)
    cur_faris.execute("""
        SELECT p.*, f.judul_faris, j.tanggal_faris, j.jam_tayang_faris, t.nama_teater_faris
        FROM pemesanan_faris p
        JOIN jadwal_faris j ON p.id_jadwal_faris=j.id_jadwal_faris
        JOIN film_faris f ON j.id_film_faris=f.id_film_faris
        JOIN teater_faris t ON j.id_teater_faris=t.id_teater_faris
        WHERE p.id_user_faris=%s
        ORDER BY p.created_at_faris DESC
    """, (session['user_id_faris'],))
    pemesanans_faris = cur_faris.fetchall()
    db_faris.close()
    return render_template('riwayat_faris.html', pemesanans_faris=pemesanans_faris)

@app.route('/struk_faris/<int:id_pemesanan_faris>')
def struk_faris(id_pemesanan_faris):
    if 'user_id_faris' not in session:
        return redirect(url_for('login_faris'))
    db_faris = get_db_faris()
    cur_faris = db_faris.cursor(dictionary=True)
    cur_faris.execute("""
        SELECT p.*, f.judul_faris, f.poster_faris, j.tanggal_faris, j.jam_tayang_faris,
               t.nama_teater_faris, u.nama_faris as nama_user_faris, u.email_faris
        FROM pemesanan_faris p
        JOIN jadwal_faris j ON p.id_jadwal_faris=j.id_jadwal_faris
        JOIN film_faris f ON j.id_film_faris=f.id_film_faris
        JOIN teater_faris t ON j.id_teater_faris=t.id_teater_faris
        JOIN user_faris u ON p.id_user_faris=u.id_user_faris
        WHERE p.id_pemesanan_faris=%s AND p.id_user_faris=%s
    """, (id_pemesanan_faris, session['user_id_faris']))
    pemesanan_faris = cur_faris.fetchone()

    if not pemesanan_faris:
        flash('Tiket tidak ditemukan!', 'danger')
        db_faris.close()
        return redirect(url_for('riwayat_faris'))

    if pemesanan_faris['status_bayar_faris'] != 'lunas':
        flash('Tiket hanya bisa dicetak setelah pembayaran lunas!', 'warning')
        db_faris.close()
        return redirect(url_for('riwayat_faris'))

    cur_faris.execute("""
        SELECT k.kode_kursi_faris FROM detail_pemesanan_faris dp
        JOIN kursi_faris k ON dp.id_kursi_faris=k.id_kursi_faris
        WHERE dp.id_pemesanan_faris=%s
        ORDER BY k.kode_kursi_faris
    """, (id_pemesanan_faris,))
    kursis_faris = cur_faris.fetchall()
    db_faris.close()

    return render_template('struk_faris.html', pemesanan_faris=pemesanan_faris, kursis_faris=kursis_faris)

@app.route('/pesan_ulang_faris/<int:id_pemesanan_faris>')
def pesan_ulang_faris(id_pemesanan_faris):
    if 'user_id_faris' not in session:
        return redirect(url_for('login_faris'))
    
    db_faris = get_db_faris()
    cur_faris = db_faris.cursor()
    
    cur_faris.execute("SELECT id_jadwal_faris FROM pemesanan_faris WHERE id_pemesanan_faris=%s", (id_pemesanan_faris,))
    result = cur_faris.fetchone()
    id_jadwal = result[0] if result else None
    
    cur_faris.execute("DELETE FROM detail_pemesanan_faris WHERE id_pemesanan_faris=%s", (id_pemesanan_faris,))
    cur_faris.execute("DELETE FROM pemesanan_faris WHERE id_pemesanan_faris=%s", (id_pemesanan_faris,))
    db_faris.commit()
    db_faris.close()
    
    if id_jadwal:
        flash('Silakan pesan ulang dengan kursi yang sama atau berbeda.', 'info')
        return redirect(url_for('pilih_kursi_faris', id_jadwal_faris=id_jadwal))
    else:
        flash('Silakan pesan tiket kembali.', 'info')
        return redirect(url_for('index_faris'))

# ==================== ADMIN ROUTES ====================
@app.route('/admin_faris')
def admin_dashboard_faris():
    if session.get('role_faris') != 'admin':
        return redirect(url_for('index_faris'))
    db_faris = get_db_faris()
    cur_faris = db_faris.cursor(dictionary=True)
    
    cur_faris.execute("SELECT COUNT(*) as t FROM user_faris WHERE role_faris='pengguna'")
    total_user_faris = cur_faris.fetchone()['t']
    cur_faris.execute("SELECT COUNT(*) as t FROM film_faris WHERE status_faris='tayang'")
    total_film_faris = cur_faris.fetchone()['t']
    cur_faris.execute("SELECT COUNT(*) as t FROM pemesanan_faris")
    total_pesan_faris = cur_faris.fetchone()['t']
    cur_faris.execute("SELECT COUNT(*) as t FROM pemesanan_faris WHERE status_bayar_faris='lunas'")
    total_lunas_faris = cur_faris.fetchone()['t']
    
    cur_faris.execute("SELECT SUM(total_harga_faris) as total FROM pemesanan_faris WHERE status_bayar_faris='lunas'")
    total_pendapatan = cur_faris.fetchone()['total'] or 0
    
    cur_faris.execute("""
        SELECT f.judul_faris, COUNT(p.id_pemesanan_faris) as jumlah_pemesanan, 
               SUM(p.total_harga_faris) as pendapatan
        FROM pemesanan_faris p
        JOIN jadwal_faris j ON p.id_jadwal_faris=j.id_jadwal_faris
        JOIN film_faris f ON j.id_film_faris=f.id_film_faris
        WHERE p.status_bayar_faris='lunas'
        GROUP BY f.id_film_faris
        ORDER BY jumlah_pemesanan DESC
        LIMIT 5
    """)
    film_terpopuler = cur_faris.fetchall()
    
    cur_faris.execute("""
        SELECT t.nama_teater_faris, COUNT(p.id_pemesanan_faris) as jumlah_transaksi,
               SUM(p.total_harga_faris) as pendapatan
        FROM pemesanan_faris p
        JOIN jadwal_faris j ON p.id_jadwal_faris=j.id_jadwal_faris
        JOIN teater_faris t ON j.id_teater_faris=t.id_teater_faris
        WHERE p.status_bayar_faris='lunas'
        GROUP BY t.id_teater_faris
        ORDER BY pendapatan DESC
    """)
    pendapatan_per_teater = cur_faris.fetchall()
    
    cur_faris.execute("""
        SELECT DATE_FORMAT(p.created_at_faris, '%Y-%m') as bulan, 
               SUM(p.total_harga_faris) as pendapatan,
               COUNT(p.id_pemesanan_faris) as jumlah_transaksi
        FROM pemesanan_faris p
        WHERE p.status_bayar_faris='lunas'
        GROUP BY DATE_FORMAT(p.created_at_faris, '%Y-%m')
        ORDER BY bulan DESC
        LIMIT 6
    """)
    pendapatan_per_bulan = cur_faris.fetchall()
    
    db_faris.close()
    return render_template('admin/dashboard_faris.html', 
                           total_user_faris=total_user_faris, 
                           total_film_faris=total_film_faris,
                           total_pesan_faris=total_pesan_faris, 
                           total_lunas_faris=total_lunas_faris,
                           total_pendapatan=total_pendapatan,
                           film_terpopuler=film_terpopuler,
                           pendapatan_per_teater=pendapatan_per_teater,
                           pendapatan_per_bulan=pendapatan_per_bulan)

@app.route('/admin_faris/film_faris')
def admin_film_faris():
    if session.get('role_faris') != 'admin':
        return redirect(url_for('index_faris'))
    db_faris = get_db_faris()
    cur_faris = db_faris.cursor(dictionary=True)
    # Admin melihat semua film (tayang & tidak_tayang) untuk keperluan manajemen
    cur_faris.execute("SELECT * FROM film_faris ORDER BY id_film_faris DESC")
    films_faris = cur_faris.fetchall()
    db_faris.close()
    return render_template('admin/film_faris.html', films_faris=films_faris)

@app.route('/admin_faris/film_faris/tambah', methods=['GET', 'POST'])
def admin_tambah_film_faris():
    if session.get('role_faris') != 'admin':
        return redirect(url_for('index_faris'))
    if request.method == 'POST':
        judul_faris = request.form['judul_faris']
        genre_faris = request.form['genre_faris']
        durasi_faris = request.form['durasi_faris']
        rating_faris = request.form['rating_faris']
        sinopsis_faris = request.form['sinopsis_faris']
        poster_faris = request.form.get('poster_faris', 'default.jpg')
        trailer_url_faris = request.form.get('trailer_url_faris', '').strip()
        status_faris = request.form.get('status_faris', 'tayang')
        db_faris = get_db_faris()
        cur_faris = db_faris.cursor()
        cur_faris.execute(
            "INSERT INTO film_faris (judul_faris, genre_faris, durasi_faris, rating_faris, sinopsis_faris, poster_faris, trailer_url_faris, status_faris) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (judul_faris, genre_faris, durasi_faris, rating_faris, sinopsis_faris, poster_faris, trailer_url_faris, status_faris)
        )
        db_faris.commit()
        db_faris.close()
        flash('Film berhasil ditambahkan!', 'success')
        return redirect(url_for('admin_film_faris'))
    return render_template('admin/tambah_film_faris.html')

@app.route('/admin_faris/film_faris/edit/<int:id_film_faris>', methods=['GET', 'POST'])
def admin_edit_film_faris(id_film_faris):
    if session.get('role_faris') != 'admin':
        return redirect(url_for('index_faris'))
    db_faris = get_db_faris()
    cur_faris = db_faris.cursor(dictionary=True)
    if request.method == 'POST':
        judul_faris = request.form['judul_faris']
        genre_faris = request.form['genre_faris']
        durasi_faris = request.form['durasi_faris']
        rating_faris = request.form['rating_faris']
        sinopsis_faris = request.form['sinopsis_faris']
        status_faris = request.form['status_faris']
        poster_faris = request.form.get('poster_faris', 'default.jpg')
        trailer_url_faris = request.form.get('trailer_url_faris', '').strip()
        cur2_faris = db_faris.cursor()
        cur2_faris.execute(
            "UPDATE film_faris SET judul_faris=%s, genre_faris=%s, durasi_faris=%s, rating_faris=%s, sinopsis_faris=%s, status_faris=%s, poster_faris=%s, trailer_url_faris=%s WHERE id_film_faris=%s",
            (judul_faris, genre_faris, durasi_faris, rating_faris, sinopsis_faris, status_faris, poster_faris, trailer_url_faris, id_film_faris)
        )
        db_faris.commit()
        db_faris.close()
        flash('Film berhasil diperbarui!', 'success')
        return redirect(url_for('admin_film_faris'))
    cur_faris.execute("SELECT * FROM film_faris WHERE id_film_faris=%s", (id_film_faris,))
    film_faris = cur_faris.fetchone()
    db_faris.close()
    return render_template('admin/edit_film_faris.html', film_faris=film_faris)

@app.route('/admin_faris/film_faris/hapus/<int:id_film_faris>')
def admin_hapus_film_faris(id_film_faris):
    if session.get('role_faris') != 'admin':
        return redirect(url_for('index_faris'))
    db_faris = get_db_faris()
    cur_faris = db_faris.cursor()
    try:
        cur_faris.execute("DELETE FROM film_faris WHERE id_film_faris=%s", (id_film_faris,))
        db_faris.commit()
        flash('Film berhasil dihapus!', 'success')
    except:
        flash('Film tidak bisa dihapus karena sudah memiliki jadwal tayang!', 'danger')
    db_faris.close()
    return redirect(url_for('admin_film_faris'))

@app.route('/admin_faris/user_faris')
def admin_user_faris():
    if session.get('role_faris') != 'admin':
        return redirect(url_for('index_faris'))
    db_faris = get_db_faris()
    cur_faris = db_faris.cursor(dictionary=True)
    cur_faris.execute("SELECT * FROM user_faris ORDER BY created_at_faris DESC")
    users_faris = cur_faris.fetchall()
    db_faris.close()
    return render_template('admin/user_faris.html', users_faris=users_faris)

@app.route('/admin_faris/user_faris/tambah', methods=['GET', 'POST'])
def admin_tambah_user_faris():
    if session.get('role_faris') != 'admin':
        return redirect(url_for('index_faris'))
    if request.method == 'POST':
        nama_faris = request.form['nama_faris']
        email_faris = request.form['email_faris']
        password_faris = request.form['password_faris']
        no_hp_faris = request.form['no_hp_faris']
        role_faris = request.form['role_faris']
        db_faris = get_db_faris()
        cur_faris = db_faris.cursor()
        try:
            cur_faris.execute(
                "INSERT INTO user_faris (nama_faris, email_faris, password_faris, no_hp_faris, role_faris) VALUES (%s,%s,%s,%s,%s)",
                (nama_faris, email_faris, password_faris, no_hp_faris, role_faris)
            )
            db_faris.commit()
            flash(f'User {role_faris} berhasil ditambahkan!', 'success')
            return redirect(url_for('admin_user_faris'))
        except Exception:
            flash('Email sudah terdaftar!', 'danger')
        finally:
            db_faris.close()
    return render_template('admin/tambah_user_faris.html')

@app.route('/admin_faris/user_faris/edit/<int:id_user_faris>', methods=['GET', 'POST'])
def admin_edit_user_faris(id_user_faris):
    if session.get('role_faris') != 'admin':
        return redirect(url_for('index_faris'))
    
    db_faris = get_db_faris()
    cur_faris = db_faris.cursor(dictionary=True)
    
    if request.method == 'POST':
        nama_faris = request.form['nama_faris']
        email_faris = request.form['email_faris']
        no_hp_faris = request.form['no_hp_faris']
        role_faris = request.form['role_faris']
        password_faris = request.form.get('password_faris', '').strip()
        
        try:
            if password_faris:
                cur_faris.execute("""
                    UPDATE user_faris 
                    SET nama_faris=%s, email_faris=%s, no_hp_faris=%s, role_faris=%s, password_faris=%s 
                    WHERE id_user_faris=%s
                """, (nama_faris, email_faris, no_hp_faris, role_faris, password_faris, id_user_faris))
            else:
                cur_faris.execute("""
                    UPDATE user_faris 
                    SET nama_faris=%s, email_faris=%s, no_hp_faris=%s, role_faris=%s 
                    WHERE id_user_faris=%s
                """, (nama_faris, email_faris, no_hp_faris, role_faris, id_user_faris))
            
            db_faris.commit()
            flash('User berhasil diupdate!', 'success')
            return redirect(url_for('admin_user_faris'))
        except Exception:
            flash('Email sudah terdaftar!', 'danger')
        finally:
            db_faris.close()
        return redirect(url_for('admin_user_faris'))
    
    cur_faris.execute("SELECT * FROM user_faris WHERE id_user_faris=%s", (id_user_faris,))
    user_faris = cur_faris.fetchone()
    db_faris.close()
    
    if not user_faris:
        flash('User tidak ditemukan!', 'danger')
        return redirect(url_for('admin_user_faris'))
    
    return render_template('admin/edit_user_faris.html', user=user_faris)

@app.route('/admin_faris/user_faris/hapus/<int:id_user_faris>')
def admin_hapus_user_faris(id_user_faris):
    if session.get('role_faris') != 'admin':
        return redirect(url_for('index_faris'))
    db_faris = get_db_faris()
    cur_faris = db_faris.cursor()
    cur_faris.execute("SELECT role_faris FROM user_faris WHERE id_user_faris=%s", (id_user_faris,))
    user = cur_faris.fetchone()
    if user and user[0] == 'admin':
        flash('Tidak bisa menghapus akun admin utama!', 'danger')
    else:
        try:
            cur_faris.execute("DELETE FROM user_faris WHERE id_user_faris=%s", (id_user_faris,))
            db_faris.commit()
            flash('User berhasil dihapus!', 'success')
        except:
            flash('User tidak bisa dihapus karena sudah memiliki pemesanan!', 'danger')
    db_faris.close()
    return redirect(url_for('admin_user_faris'))

@app.route('/admin_faris/kursi_faris')
def admin_kursi_faris():
    if session.get('role_faris') != 'admin':
        return redirect(url_for('index_faris'))
    db_faris = get_db_faris()
    cur_faris = db_faris.cursor(dictionary=True)
    cur_faris.execute("SELECT * FROM teater_faris")
    teaters_faris = cur_faris.fetchall()
    
    for t in teaters_faris:
        cur_faris.execute("SELECT * FROM kursi_faris WHERE id_teater_faris=%s ORDER BY kode_kursi_faris", (t['id_teater_faris'],))
        t['kursi_faris'] = cur_faris.fetchall()
    db_faris.close()
    return render_template('admin/kursi_faris.html', teaters_faris=teaters_faris)

@app.route('/admin_faris/kursi_faris/tambah/<int:id_teater_faris>', methods=['POST'])
def admin_tambah_kursi_faris(id_teater_faris):
    if session.get('role_faris') != 'admin':
        return redirect(url_for('index_faris'))
    kode_kursi = request.form['kode_kursi_faris']
    db_faris = get_db_faris()
    cur_faris = db_faris.cursor()
    try:
        cur_faris.execute("INSERT INTO kursi_faris (id_teater_faris, kode_kursi_faris) VALUES (%s,%s)", 
                         (id_teater_faris, kode_kursi))
        db_faris.commit()
        flash('Kursi berhasil ditambahkan!', 'success')
    except:
        flash('Kode kursi sudah ada!', 'danger')
    db_faris.close()
    return redirect(url_for('admin_kursi_faris'))

@app.route('/admin_faris/kursi_faris/hapus/<int:id_kursi_faris>')
def admin_hapus_kursi_faris(id_kursi_faris):
    if session.get('role_faris') != 'admin':
        return redirect(url_for('index_faris'))
    db_faris = get_db_faris()
    cur_faris = db_faris.cursor()
    try:
        cur_faris.execute("DELETE FROM kursi_faris WHERE id_kursi_faris=%s", (id_kursi_faris,))
        db_faris.commit()
        flash('Kursi berhasil dihapus!', 'success')
    except:
        flash('Kursi tidak bisa dihapus karena sudah ada pemesanan!', 'danger')
    db_faris.close()
    return redirect(url_for('admin_kursi_faris'))

@app.route('/admin_faris/jadwal_faris')
def admin_jadwal_faris():
    if session.get('role_faris') != 'admin':
        return redirect(url_for('index_faris'))
    db_faris = get_db_faris()
    cur_faris = db_faris.cursor(dictionary=True)
    cur_faris.execute("""
        SELECT j.*, f.judul_faris, t.nama_teater_faris FROM jadwal_faris j
        JOIN film_faris f ON j.id_film_faris=f.id_film_faris
        JOIN teater_faris t ON j.id_teater_faris=t.id_teater_faris
        ORDER BY j.tanggal_faris DESC, j.jam_tayang_faris
    """)
    jadwals_faris = cur_faris.fetchall()
    db_faris.close()
    return render_template('admin/jadwal_faris.html', jadwals_faris=jadwals_faris)

# ==================== PENGELOLA ROUTES ====================
@app.route('/pengelola_faris')
def pengelola_dashboard_faris():
    if session.get('role_faris') != 'pengelola':
        return redirect(url_for('index_faris'))
    
    db_faris = get_db_faris()
    cur_faris = db_faris.cursor(dictionary=True)
    
    # Total jadwal
    cur_faris.execute("SELECT COUNT(*) as t FROM jadwal_faris")
    total_jadwal_faris = cur_faris.fetchone()['t']
    
    # Total teater
    cur_faris.execute("SELECT COUNT(*) as t FROM teater_faris")
    total_teater_faris = cur_faris.fetchone()['t']
    
    # Total pendapatan (dari pemesanan yang lunas)
    cur_faris.execute("SELECT SUM(total_harga_faris) as total FROM pemesanan_faris WHERE status_bayar_faris='lunas'")
    total_pendapatan_faris = cur_faris.fetchone()['total'] or 0
    
    db_faris.close()
    
    return render_template('pengelola/dashboard_faris.html',
                           total_jadwal_faris=total_jadwal_faris,
                           total_teater_faris=total_teater_faris,
                           total_pendapatan_faris=total_pendapatan_faris)

@app.route('/pengelola_faris/force_lunas/<int:id_pemesanan_faris>')
def pengelola_force_lunas_faris(id_pemesanan_faris):
    if session.get('role_faris') != 'pengelola':
        flash('Hanya pengelola yang bisa akses!', 'danger')
        return redirect(url_for('index_faris'))
    
    db_faris = get_db_faris()
    cur_faris = db_faris.cursor()
    
    cur_faris.execute("UPDATE pemesanan_faris SET status_bayar_faris='lunas' WHERE id_pemesanan_faris=%s", (id_pemesanan_faris,))
    db_faris.commit()
    
    cur_faris.execute("SELECT id_jadwal_faris FROM pemesanan_faris WHERE id_pemesanan_faris=%s", (id_pemesanan_faris,))
    result = cur_faris.fetchone()
    if result:
        update_status_jadwal(result[0])
    
    db_faris.close()
    
    flash('Pembayaran berhasil diverifikasi! Tiket bisa dicetak.', 'success')
    return redirect(url_for('pengelola_verifikasi_faris'))

# ==================== PENGELOLA CRUD JADWAL ====================

# 1. MENAMPILKAN DAFTAR JADWAL
@app.route('/pengelola_faris/jadwal_faris')
def pengelola_jadwal_faris():
    # Halaman daftar jadwal dihapus sesuai revisi penguji.
    # Route ini tetap ada agar link lama tidak error, langsung ke form tambah jadwal.
    return redirect(url_for('pengelola_tambah_jadwal_faris'))


# 2. GENERATE JADWAL OTOMATIS - 1 studio, 1 film per hari
JAM_MULAI_PERTAMA = "09:00"   # Showtime pertama setiap hari
BATAS_SELESAI     = "23:00"   # Showtime terakhir harus selesai sebelum jam ini
JEDA_MENIT        = 10        # Jeda bersih layar antar sesi
HARGA_WEEKDAY_DEFAULT = 35000
HARGA_WEEKEND_DEFAULT = 50000
MAKS_ADVANCE_HARI = 30        # Advance booking maksimal 30 hari ke depan

def hitung_slot_jam(durasi_film):
    """
    Kembalikan list string jam mulai '09:00', '11:50', ...
    Syarat: film SELESAI (jam_mulai + durasi) paling lambat jam 23:00.
    Jeda bersih layar (JEDA_MENIT) hanya untuk jarak antar slot, bukan batas akhir.
    """
    slot = []
    menit  = 9 * 60       # mulai 09:00
    batas  = 23 * 60      # film harus SELESAI sebelum/tepat 23:00
    durasi = int(durasi_film)
    while True:
        selesai_film = menit + durasi      # menit ketika film selesai
        if selesai_film > batas:           # lewat 23:00 -> stop
            break
        jam = str(menit // 60).zfill(2)
        mnt = str(menit %  60).zfill(2)
        slot.append(f"{jam}:{mnt}")
        menit = selesai_film + JEDA_MENIT  # slot berikutnya setelah jeda
    return slot

def generate_jadwal_rentang(id_teater_faris, id_film_faris,
                             tgl_mulai, tgl_selesai,
                             harga_weekday_faris, harga_weekend_faris):
    """
    Generate jadwal otomatis untuk rentang tgl_mulai .. tgl_selesai.
    Aturan: 1 studio = 1 film per hari.
    Kembalikan (total_berhasil, list_skip, list_error).
    """
    db = get_db_faris()
    cur = db.cursor(dictionary=True)

    # Ambil durasi film sekali
    cur.execute("SELECT judul_faris, durasi_faris FROM film_faris WHERE id_film_faris=%s", (id_film_faris,))
    film = cur.fetchone()
    if not film:
        db.close()
        return 0, [], ["Film tidak ditemukan."]
    durasi_film = film['durasi_faris']
    judul_film  = film['judul_faris']

    slot_jam = hitung_slot_jam(durasi_film)
    if not slot_jam:
        db.close()
        return 0, [], [f"Film '{judul_film}' ({durasi_film} menit) terlalu panjang untuk dijadwalkan dalam satu hari."]

    # Ambil semua tanggal dalam rentang yang studio ini sudah ada jadwal (agar bisa di-skip)
    cur.execute("""
        SELECT tanggal_faris, f.judul_faris as judul_existing
        FROM jadwal_faris j
        JOIN film_faris f ON j.id_film_faris = f.id_film_faris
        WHERE j.id_teater_faris = %s
          AND j.tanggal_faris BETWEEN %s AND %s
        GROUP BY j.tanggal_faris
    """, (id_teater_faris, tgl_mulai, tgl_selesai))
    sudah_ada = {row['tanggal_faris']: row['judul_existing'] for row in cur.fetchall()}

    cur2 = db.cursor()
    total_berhasil = 0
    list_skip      = []
    list_error     = []

    tgl = tgl_mulai
    while tgl <= tgl_selesai:
        if tgl in sudah_ada:
            list_skip.append(f"{tgl.strftime('%d/%m/%Y')} → sudah ada '{sudah_ada[tgl]}'")
        else:
            try:
                for jam in slot_jam:
                    cur2.execute(
                        """INSERT INTO jadwal_faris
                           (id_film_faris, id_teater_faris, tanggal_faris,
                            jam_tayang_faris, harga_weekday_faris, harga_weekend_faris, status_faris)
                           VALUES (%s,%s,%s,%s,%s,%s,'tersedia')""",
                        (id_film_faris, id_teater_faris, tgl,
                         jam, harga_weekday_faris, harga_weekend_faris)
                    )
                db.commit()
                total_berhasil += 1
            except Exception as e:
                db.rollback()
                list_error.append(f"{tgl.strftime('%d/%m/%Y')} → error: {e}")
        tgl += timedelta(days=1)

    db.close()
    return total_berhasil, list_skip, list_error


@app.route('/pengelola_faris/jadwal_faris/tambah', methods=['GET', 'POST'])
def pengelola_tambah_jadwal_faris():
    if session.get('role_faris') != 'pengelola':
        return redirect(url_for('index_faris'))

    db_faris = get_db_faris()
    cur_faris = db_faris.cursor(dictionary=True)

    if request.method == 'POST':
        id_film_faris      = request.form['id_film_faris']
        id_teater_faris    = request.form['id_teater_faris']
        tgl_mulai_str      = request.form['tanggal_mulai_faris']
        tgl_selesai_str    = request.form['tanggal_selesai_faris']
        harga_weekday_faris = int(request.form.get('harga_weekday_faris', HARGA_WEEKDAY_DEFAULT))
        harga_weekend_faris = int(request.form.get('harga_weekend_faris', HARGA_WEEKEND_DEFAULT))

        try:
            tgl_mulai  = datetime.strptime(tgl_mulai_str,  '%Y-%m-%d').date()
            tgl_selesai = datetime.strptime(tgl_selesai_str, '%Y-%m-%d').date()
        except ValueError:
            flash('Format tanggal tidak valid!', 'danger')
            tgl_mulai = tgl_selesai = None

        if tgl_mulai and tgl_selesai:
            hari_ini  = datetime.now().date()
            batas_max = hari_ini + timedelta(days=MAKS_ADVANCE_HARI)

            if tgl_mulai < hari_ini:
                flash('Tanggal mulai tidak boleh di masa lalu!', 'danger')
            elif tgl_selesai < tgl_mulai:
                flash('Tanggal selesai tidak boleh sebelum tanggal mulai!', 'danger')
            elif tgl_selesai > batas_max:
                flash(f'Jadwal maksimal {MAKS_ADVANCE_HARI} hari ke depan (sampai {batas_max.strftime("%d/%m/%Y")})!', 'danger')
            else:
                db_faris.close()
                total_hari = (tgl_selesai - tgl_mulai).days + 1
                berhasil, skips, errors = generate_jadwal_rentang(
                    id_teater_faris, id_film_faris,
                    tgl_mulai, tgl_selesai,
                    harga_weekday_faris, harga_weekend_faris
                )
                if berhasil > 0:
                    flash(f'✅ Berhasil membuat jadwal untuk {berhasil} hari '
                          f'(dari {total_hari} hari yang diminta).', 'success')
                if skips:
                    flash('⚠️ Dilewati karena sudah ada jadwal: ' + ' | '.join(skips), 'warning')
                if errors:
                    flash('❌ Gagal: ' + ' | '.join(errors), 'danger')
                if berhasil == 0 and not errors:
                    flash('Semua tanggal dalam rentang ini sudah memiliki jadwal.', 'info')
                return redirect(url_for('pengelola_dashboard_faris'))
            # kalau validasi gagal, buka kembali koneksi untuk render form
            db_faris = get_db_faris()
            cur_faris = db_faris.cursor(dictionary=True)

    # GET — tampilkan form
    # Hanya film yang statusnya 'tayang' (diaktifkan oleh admin).
    # Film 'tidak_tayang' tidak boleh dijadwalkan.
    cur_faris.execute("SELECT * FROM film_faris WHERE status_faris = 'tayang' ORDER BY judul_faris")
    films_faris = cur_faris.fetchall()
    cur_faris.execute("SELECT * FROM teater_faris ORDER BY nama_teater_faris")
    teaters_faris = cur_faris.fetchall()

    # Kalender ketersediaan studio (30 hari ke depan) — untuk preview di form
    hari_ini  = datetime.now().date()
    batas_max = hari_ini + timedelta(days=MAKS_ADVANCE_HARI)
    cur_faris.execute("""
        SELECT j.id_teater_faris, j.tanggal_faris,
               t.nama_teater_faris, f.judul_faris
        FROM jadwal_faris j
        JOIN film_faris  f ON j.id_film_faris   = f.id_film_faris
        JOIN teater_faris t ON j.id_teater_faris = t.id_teater_faris
        WHERE j.tanggal_faris BETWEEN %s AND %s
        GROUP BY j.id_teater_faris, j.tanggal_faris
        ORDER BY j.tanggal_faris, t.nama_teater_faris
    """, (hari_ini, batas_max))
    jadwal_existing = cur_faris.fetchall()
    db_faris.close()

    return render_template('pengelola/tambah_jadwal_faris.html',
                           films_faris=films_faris,
                           teaters_faris=teaters_faris,
                           jadwal_existing=jadwal_existing,
                           maks_advance=MAKS_ADVANCE_HARI,
                           hari_ini=hari_ini.strftime('%Y-%m-%d'),
                           batas_max=batas_max.strftime('%Y-%m-%d'))


# 3. EDIT JADWAL
@app.route('/pengelola_faris/jadwal_faris/edit/<int:id_jadwal_faris>', methods=['GET', 'POST'])
def pengelola_edit_jadwal_faris(id_jadwal_faris):
    if session.get('role_faris') != 'pengelola':
        return redirect(url_for('index_faris'))
    
    db_faris = get_db_faris()
    cur_faris = db_faris.cursor(dictionary=True)
    
    if request.method == 'POST':
        id_film_faris = request.form['id_film_faris']
        id_teater_faris = request.form['id_teater_faris']
        tanggal_faris = request.form['tanggal_faris']
        jam_faris = request.form['jam_tayang_faris']
        harga_weekday_faris = request.form['harga_weekday_faris']
        harga_weekend_faris = request.form['harga_weekend_faris']
        
        tgl_faris = datetime.strptime(tanggal_faris, '%Y-%m-%d').date()
        if tgl_faris < datetime.now().date():
            flash('Tanggal tidak boleh di masa lalu!', 'danger')
        else:
            cur_faris.execute("SELECT durasi_faris FROM film_faris WHERE id_film_faris=%s", (id_film_faris,))
            film_data = cur_faris.fetchone()
            durasi_film = film_data['durasi_faris'] if film_data else 120
            
            bentrok, pesan_bentrok = cek_bentrok_jadwal(id_teater_faris, tanggal_faris, jam_faris, durasi_film, id_jadwal_faris)
            
            if bentrok:
                flash(f'Jadwal bentrok! {pesan_bentrok}', 'danger')
            else:
                cur2_faris = db_faris.cursor()
                cur2_faris.execute("""
                    UPDATE jadwal_faris 
                    SET id_film_faris=%s, id_teater_faris=%s, tanggal_faris=%s, jam_tayang_faris=%s, 
                        harga_weekday_faris=%s, harga_weekend_faris=%s
                    WHERE id_jadwal_faris=%s
                """, (id_film_faris, id_teater_faris, tanggal_faris, jam_faris, 
                      harga_weekday_faris, harga_weekend_faris, id_jadwal_faris))
                db_faris.commit()
                flash('Jadwal berhasil diupdate!', 'success')
                db_faris.close()
                return redirect(url_for('pengelola_dashboard_faris'))
    
    # GET request - tampilkan form edit
    cur_faris.execute("""
        SELECT j.*, f.judul_faris, f.durasi_faris 
        FROM jadwal_faris j
        JOIN film_faris f ON j.id_film_faris = f.id_film_faris
        WHERE j.id_jadwal_faris=%s
    """, (id_jadwal_faris,))
    jadwal = cur_faris.fetchone()
    cur_faris.execute("SELECT * FROM film_faris WHERE status_faris='tayang'")
    films_faris = cur_faris.fetchall()
    cur_faris.execute("SELECT * FROM teater_faris")
    teaters_faris = cur_faris.fetchall()
    db_faris.close()
    
    return render_template('pengelola/edit_jadwal_faris.html', 
                           jadwal=jadwal, 
                           films_faris=films_faris, 
                           teaters_faris=teaters_faris)


# 4. HAPUS JADWAL
@app.route('/pengelola_faris/jadwal_faris/hapus/<int:id_jadwal_faris>')
def pengelola_hapus_jadwal_faris(id_jadwal_faris):
    if session.get('role_faris') != 'pengelola':
        return redirect(url_for('index_faris'))
    
    db_faris = get_db_faris()
    cur_faris = db_faris.cursor()
    
    try:
        cur_faris.execute("DELETE FROM jadwal_faris WHERE id_jadwal_faris=%s", (id_jadwal_faris,))
        db_faris.commit()
        flash('Jadwal berhasil dihapus!', 'success')
    except:
        flash('Jadwal tidak bisa dihapus karena sudah ada pemesanan!', 'danger')
    
    db_faris.close()
    return redirect(url_for('pengelola_dashboard_faris'))

@app.route('/pengelola_faris/teater_faris')
def pengelola_teater_faris():
    if session.get('role_faris') != 'pengelola':
        return redirect(url_for('index_faris'))
    db_faris = get_db_faris()
    cur_faris = db_faris.cursor(dictionary=True)
    cur_faris.execute("SELECT * FROM teater_faris")
    teaters_faris = cur_faris.fetchall()
    db_faris.close()
    return render_template('pengelola/teater_faris.html', teaters_faris=teaters_faris)

@app.route('/pengelola_faris/teater_faris/edit/<int:id_teater_faris>', methods=['GET', 'POST'])
def pengelola_edit_teater_faris(id_teater_faris):
    if session.get('role_faris') != 'pengelola':
        return redirect(url_for('index_faris'))
    db_faris = get_db_faris()
    cur_faris = db_faris.cursor(dictionary=True)
    if request.method == 'POST':
        nama_faris = request.form['nama_teater_faris']
        kapasitas_baru = int(request.form['kapasitas_faris'])
        # Ambil kapasitas lama
        cur_faris.execute("SELECT kapasitas_faris FROM teater_faris WHERE id_teater_faris=%s", (id_teater_faris,))
        teater_lama = cur_faris.fetchone()
        kapasitas_lama = teater_lama['kapasitas_faris'] if teater_lama else 0
        cur2_faris = db_faris.cursor()
        cur2_faris.execute(
            "UPDATE teater_faris SET nama_teater_faris=%s, kapasitas_faris=%s WHERE id_teater_faris=%s",
            (nama_faris, kapasitas_baru, id_teater_faris)
        )
        # Jika kapasitas bertambah, tambah kursi baru
        if kapasitas_baru > kapasitas_lama:
            cur_faris.execute("""
                SELECT kode_kursi_faris FROM kursi_faris 
                WHERE id_teater_faris=%s 
                ORDER BY LEFT(kode_kursi_faris,1), CAST(SUBSTRING(kode_kursi_faris,2) AS UNSIGNED)
            """, (id_teater_faris,))
            existing = {r['kode_kursi_faris'] for r in cur_faris.fetchall()}
            huruf_baris = [chr(i) for i in range(ord('A'), ord('Z') + 1)]
            kursi_dibuat = 0
            for baris in huruf_baris:
                for nomor in range(1, 21):
                    if len(existing) + kursi_dibuat >= kapasitas_baru:
                        break
                    kode = f"{baris}{nomor}"
                    if kode not in existing:
                        cur2_faris.execute(
                            "INSERT INTO kursi_faris (id_teater_faris, kode_kursi_faris) VALUES (%s,%s)",
                            (id_teater_faris, kode)
                        )
                        kursi_dibuat += 1
                if len(existing) + kursi_dibuat >= kapasitas_baru:
                    break
        # Jika kapasitas berkurang, hapus kursi yang tidak ada pemesanan (dari belakang)
        elif kapasitas_baru < kapasitas_lama:
            cur_faris.execute("""
                SELECT k.id_kursi_faris, k.kode_kursi_faris FROM kursi_faris k
                LEFT JOIN detail_pemesanan_faris dp ON k.id_kursi_faris = dp.id_kursi_faris
                WHERE k.id_teater_faris=%s AND dp.id_kursi_faris IS NULL
                ORDER BY LEFT(k.kode_kursi_faris,1) DESC, CAST(SUBSTRING(k.kode_kursi_faris,2) AS UNSIGNED) DESC
            """, (id_teater_faris,))
            kursi_hapus_kandidat = cur_faris.fetchall()
            selisih = kapasitas_lama - kapasitas_baru
            for i, k in enumerate(kursi_hapus_kandidat):
                if i >= selisih:
                    break
                cur2_faris.execute("DELETE FROM kursi_faris WHERE id_kursi_faris=%s", (k['id_kursi_faris'],))
        db_faris.commit()
        db_faris.close()
        flash('Teater berhasil diperbarui!', 'success')
        return redirect(url_for('pengelola_teater_faris'))
    cur_faris.execute("SELECT * FROM teater_faris WHERE id_teater_faris=%s", (id_teater_faris,))
    teater = cur_faris.fetchone()
    db_faris.close()
    if not teater:
        flash('Teater tidak ditemukan!', 'danger')
        return redirect(url_for('pengelola_teater_faris'))
    return render_template('pengelola/edit_teater_faris.html', teater=teater)

@app.route('/pengelola_faris/teater_faris/tambah', methods=['GET', 'POST'])
def pengelola_tambah_teater_faris():
    if session.get('role_faris') != 'pengelola':
        return redirect(url_for('index_faris'))
    if request.method == 'POST':
        nama_faris = request.form['nama_teater_faris']
        kapasitas_faris = int(request.form['kapasitas_faris'])
        db_faris = get_db_faris()
        cur2_faris = db_faris.cursor()
        cur2_faris.execute("INSERT INTO teater_faris (nama_teater_faris, kapasitas_faris) VALUES (%s,%s)", (nama_faris, kapasitas_faris))
        db_faris.commit()
        id_teater_faris = cur2_faris.lastrowid

        huruf_baris_faris = [chr(i) for i in range(ord('A'), ord('Z') + 1)]
        kursi_dibuat_faris = 0
        baris_idx_faris = 0
        nomor_kursi_faris = 1
        while kursi_dibuat_faris < kapasitas_faris:
            if baris_idx_faris >= len(huruf_baris_faris):
                break
            baris_huruf_faris = huruf_baris_faris[baris_idx_faris]
            cur2_faris.execute("INSERT INTO kursi_faris (id_teater_faris, kode_kursi_faris) VALUES (%s,%s)",
                         (id_teater_faris, f"{baris_huruf_faris}{nomor_kursi_faris}"))
            kursi_dibuat_faris += 1
            nomor_kursi_faris += 1
            if nomor_kursi_faris > 20:
                baris_idx_faris += 1
                nomor_kursi_faris = 1
        db_faris.commit()
        db_faris.close()
        flash('Teater berhasil ditambahkan!', 'success')
        return redirect(url_for('pengelola_teater_faris'))
    return render_template('pengelola/tambah_teater_faris.html')

@app.route('/pengelola_faris/laporan_keuangan')
@role_required('pengelola')
def pengelola_laporan_keuangan_faris():
    db_faris = get_db_faris()
    cur_faris = db_faris.cursor(dictionary=True)

    # ── Ambil semua studio untuk dropdown ───────────────────────
    cur_faris.execute("SELECT id_teater_faris, nama_teater_faris FROM teater_faris ORDER BY nama_teater_faris")
    semua_studio = cur_faris.fetchall()

    # ── Parameter filter ─────────────────────────────────────────
    id_studio   = request.args.get('id_studio', '')      # '' = semua studio
    start_date  = request.args.get('start_date', '')
    end_date    = request.args.get('end_date', '')
    periode     = request.args.get('periode', '')
    today       = datetime.now().date()

    # Shortcut periode
    if periode == 'hari':
        start_date = end_date = today.strftime('%Y-%m-%d')
    elif periode == 'bulan':
        start_date = today.replace(day=1).strftime('%Y-%m-%d')
        end_date   = today.strftime('%Y-%m-%d')
    elif periode == 'tahun':
        start_date = today.replace(month=1, day=1).strftime('%Y-%m-%d')
        end_date   = today.strftime('%Y-%m-%d')

    # Label periode
    if start_date and end_date:
        s = datetime.strptime(start_date, '%Y-%m-%d').date()
        e = datetime.strptime(end_date,   '%Y-%m-%d').date()
        if s == e:
            periode_text = s.strftime('%d %B %Y')
        elif s.year == e.year:
            periode_text = f"{s.strftime('%d %B')} – {e.strftime('%d %B %Y')}"
        else:
            periode_text = f"{s.strftime('%d %B %Y')} – {e.strftime('%d %B %Y')}"
    else:
        periode_text = "Semua Data"

    # Label studio
    studio_text = "Semua Studio"
    if id_studio:
        for s in semua_studio:
            if str(s['id_teater_faris']) == str(id_studio):
                studio_text = s['nama_teater_faris']
                break

    # ── Bangun WHERE clause ───────────────────────────────────────
    where_parts = ["p.status_bayar_faris = 'lunas'"]
    params: list = []
    if id_studio:
        where_parts.append("j.id_teater_faris = %s")
        params.append(id_studio)
    if start_date and end_date:
        where_parts.append("DATE(p.created_at_faris) BETWEEN %s AND %s")
        params += [start_date, end_date]
    where_sql = "WHERE " + " AND ".join(where_parts)

    # ── A. Summary card: total pendapatan studio yang dipilih ────
    cur_faris.execute(f"""
        SELECT
            t.id_teater_faris,
            t.nama_teater_faris,
            COUNT(DISTINCT p.id_pemesanan_faris) AS jml_transaksi,
            COALESCE(SUM(p.total_harga_faris), 0) AS total_pendapatan
        FROM pemesanan_faris p
        JOIN jadwal_faris j ON p.id_jadwal_faris = j.id_jadwal_faris
        JOIN teater_faris t ON j.id_teater_faris  = t.id_teater_faris
        {where_sql}
        GROUP BY t.id_teater_faris
        ORDER BY t.nama_teater_faris
    """, params)
    rekap_studio = cur_faris.fetchall()

    # ── B. Rincian per hari (untuk studio terpilih) ───────────────
    cur_faris.execute(f"""
        SELECT
            DATE(j.tanggal_faris)                    AS tgl_tayang,
            t.nama_teater_faris,
            f.judul_faris,
            COUNT(DISTINCT p.id_pemesanan_faris)     AS jml_transaksi,
            COALESCE(SUM(p.total_harga_faris), 0)    AS pendapatan
        FROM pemesanan_faris p
        JOIN jadwal_faris j ON p.id_jadwal_faris = j.id_jadwal_faris
        JOIN film_faris   f ON j.id_film_faris    = f.id_film_faris
        JOIN teater_faris t ON j.id_teater_faris  = t.id_teater_faris
        {where_sql}
        GROUP BY tgl_tayang, t.id_teater_faris, f.id_film_faris
        ORDER BY tgl_tayang DESC, t.nama_teater_faris
    """, params)
    ringkasan_faris = cur_faris.fetchall()

    # ── C. Detail transaksi ───────────────────────────────────────
    cur_faris.execute(f"""
        SELECT
            p.id_pemesanan_faris,
            p.created_at_faris,
            p.total_harga_faris,
            u.nama_faris,
            f.judul_faris,
            t.nama_teater_faris,
            DATE(j.tanggal_faris)  AS tgl_tayang,
            j.jam_tayang_faris,
            GROUP_CONCAT(k.kode_kursi_faris ORDER BY k.kode_kursi_faris SEPARATOR ', ') AS kursi
        FROM pemesanan_faris p
        JOIN user_faris   u  ON p.id_user_faris   = u.id_user_faris
        JOIN jadwal_faris j  ON p.id_jadwal_faris  = j.id_jadwal_faris
        JOIN film_faris   f  ON j.id_film_faris    = f.id_film_faris
        JOIN teater_faris t  ON j.id_teater_faris  = t.id_teater_faris
        LEFT JOIN detail_pemesanan_faris dp ON dp.id_pemesanan_faris = p.id_pemesanan_faris
        LEFT JOIN kursi_faris k ON dp.id_kursi_faris = k.id_kursi_faris
        {where_sql}
        GROUP BY p.id_pemesanan_faris
        ORDER BY p.created_at_faris DESC
    """, params)
    laporan_faris = cur_faris.fetchall()

    grand_total = sum(r['total_pendapatan'] for r in rekap_studio) if rekap_studio else 0
    now_date    = datetime.now().strftime('%d %B %Y %H:%M')
    db_faris.close()

    return render_template('pengelola/laporan_keuangan_faris.html',
                           semua_studio=semua_studio,
                           rekap_studio=rekap_studio,
                           ringkasan_faris=ringkasan_faris,
                           laporan_faris=laporan_faris,
                           grand_total=grand_total,
                           id_studio=id_studio,
                           studio_text=studio_text,
                           start_date=start_date,
                           end_date=end_date,
                           periode_text=periode_text,
                           now_date=now_date)


if __name__ == '__main__':
    app.run(debug=True)