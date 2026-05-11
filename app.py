from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import mysql.connector
import os
import uuid
from datetime import datetime, timedelta, date
from werkzeug.utils import secure_filename
import midtransclient
import requests
import base64
import json

app = Flask(__name__)
app.secret_key = 'cgvbioskop2024rahasia_faris'

UPLOAD_FOLDER_FARIS = os.path.join('static', 'uploads')
ALLOWED_EXTENSIONS_FARIS = {'png', 'jpg', 'jpeg'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER_FARIS

if not os.path.exists(UPLOAD_FOLDER_FARIS):
    os.makedirs(UPLOAD_FOLDER_FARIS)

# Konfigurasi Midtrans
MIDTRANS_SERVER_KEY = 'Mid-server-gMZxTVNEDZE4fbHnaz9qYv9T'
MIDTRANS_CLIENT_KEY = 'Mid-client-PvwImZjz6-_b08sv'
MIDTRANS_MERCHANT_ID = 'M670785059'
MIDTRANS_IS_PRODUCTION = False

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
    db_faris = get_db_faris()
    cur_faris = db_faris.cursor(dictionary=True)
    if keyword_faris:
        cur_faris.execute("""
            SELECT * FROM film_faris
            WHERE status_faris='tayang'
              AND (judul_faris LIKE %s OR genre_faris LIKE %s)
            ORDER BY judul_faris
        """, (f'%{keyword_faris}%', f'%{keyword_faris}%'))
    else:
        cur_faris.execute("SELECT * FROM film_faris WHERE status_faris='tayang' ORDER BY id_film_faris DESC")
    films_faris = cur_faris.fetchall()
    db_faris.close()
    return render_template('index_faris.html', films_faris=films_faris, keyword_faris=keyword_faris)

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
    
    hari_ini_faris = datetime.now().date()
    batas_faris = hari_ini_faris + timedelta(days=4)
    
    cur_faris.execute("""
        SELECT j.*, t.nama_teater_faris
        FROM jadwal_faris j
        JOIN teater_faris t ON j.id_teater_faris = t.id_teater_faris
        WHERE j.id_film_faris = %s 
          AND j.tanggal_faris BETWEEN %s AND %s
        ORDER BY j.tanggal_faris, j.jam_tayang_faris
    """, (id_film_faris, hari_ini_faris, batas_faris))
    
    jadwals_faris = cur_faris.fetchall()
    db_faris.close()
    
    return render_template('detail_film_faris.html', 
                           film_faris=film_faris, 
                           jadwals_faris=jadwals_faris)

@app.route('/pilih_kursi_faris/<int:id_jadwal_faris>')
def pilih_kursi_faris(id_jadwal_faris):
    if 'user_id_faris' not in session or session.get('role_faris') != 'pengguna':
        flash('Silakan login sebagai pengguna dulu!', 'warning')
        return redirect(url_for('login_faris'))

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

    cur_faris.execute("""
        SELECT dp.id_kursi_faris FROM detail_pemesanan_faris dp
        JOIN pemesanan_faris p ON dp.id_pemesanan_faris=p.id_pemesanan_faris
        WHERE p.id_jadwal_faris=%s AND p.status_bayar_faris='lunas'
    """, (id_jadwal_faris,))
    kursi_dipesan_faris = [r['id_kursi_faris'] for r in cur_faris.fetchall()]

    #harga berdasarkan hari
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
                           total_kolom_faris=total_kolom_faris)

@app.route('/pesan_faris', methods=['POST'])
def pesan_faris():
    if 'user_id_faris' not in session:
        return redirect(url_for('login_faris'))

    id_jadwal_faris = request.form['id_jadwal_faris']
    kursi_dipilih_faris = request.form.getlist('kursi_faris')

    if not kursi_dipilih_faris:
        flash('Pilih minimal 1 kursi!', 'warning')
        return redirect(url_for('pilih_kursi_faris', id_jadwal_faris=id_jadwal_faris))

    db_faris = get_db_faris()
    cur_faris = db_faris.cursor(dictionary=True)
    cur_faris.execute("SELECT * FROM jadwal_faris WHERE id_jadwal_faris=%s", (id_jadwal_faris,))
    jadwal_faris = cur_faris.fetchone()
    cur_faris.execute("SELECT * FROM user_faris WHERE id_user_faris=%s", (session['user_id_faris'],))
    user_faris = cur_faris.fetchone()
    
    tanggal_faris = jadwal_faris['tanggal_faris']
    hari_faris = tanggal_faris.weekday()
    harga_faris = jadwal_faris['harga_weekend_faris'] if hari_faris in [5, 6] else jadwal_faris['harga_weekday_faris']
    total_faris = harga_faris * len(kursi_dipilih_faris)
    
    cur2_faris = db_faris.cursor()
    cur2_faris.execute(
        "INSERT INTO pemesanan_faris (id_user_faris, id_jadwal_faris, total_harga_faris, status_bayar_faris) VALUES (%s,%s,%s,'belum')",
        (session['user_id_faris'], id_jadwal_faris, total_faris)
    )
    db_faris.commit()
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
    db_faris.close()
    
    midtrans_response = create_midtrans_transaction(
        id_pemesanan_faris, total_faris, user_faris['nama_faris'],
        user_faris['email_faris'], user_faris['no_hp_faris'], kursi_data
    )
    
    if midtrans_response and 'token' in midtrans_response:
        order_id_value = midtrans_response.get('order_id', f"CGV-{id_pemesanan_faris}")
        db_faris = get_db_faris()
        cur_faris = db_faris.cursor()
        cur_faris.execute("UPDATE pemesanan_faris SET snap_token=%s, midtrans_order_id=%s, status_bayar_faris='pending' WHERE id_pemesanan_faris=%s",
                         (midtrans_response['token'], order_id_value, id_pemesanan_faris))
        db_faris.commit()
        db_faris.close()
        return render_template('pay_faris.html', 
                               snap_token=midtrans_response['token'],
                               midtrans_client_key=MIDTRANS_CLIENT_KEY,
                               id_pemesanan_faris=id_pemesanan_faris,
                               total_harga=total_faris)
    else:
        flash('Gagal memproses pembayaran. Silakan coba lagi.', 'danger')
        db_faris = get_db_faris()
        cur_faris = db_faris.cursor()
        cur_faris.execute("DELETE FROM detail_pemesanan_faris WHERE id_pemesanan_faris=%s", (id_pemesanan_faris,))
        cur_faris.execute("DELETE FROM pemesanan_faris WHERE id_pemesanan_faris=%s", (id_pemesanan_faris,))
        db_faris.commit()
        db_faris.close()
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
        db_faris = get_db_faris()
        cur_faris = db_faris.cursor()
        cur_faris.execute(
            "INSERT INTO film_faris (judul_faris, genre_faris, durasi_faris, rating_faris, sinopsis_faris, poster_faris, trailer_url_faris) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (judul_faris, genre_faris, durasi_faris, rating_faris, sinopsis_faris, poster_faris, trailer_url_faris)
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
    if session.get('role_faris') != 'pengelola':
        return redirect(url_for('index_faris'))
    
    db_faris = get_db_faris()
    cur_faris = db_faris.cursor(dictionary=True)
    
    cur_faris.execute("""
        SELECT j.*, f.judul_faris, f.durasi_faris, t.nama_teater_faris 
        FROM jadwal_faris j
        JOIN film_faris f ON j.id_film_faris = f.id_film_faris
        JOIN teater_faris t ON j.id_teater_faris = t.id_teater_faris
        ORDER BY j.tanggal_faris DESC, j.jam_tayang_faris
    """)
    jadwals_faris = cur_faris.fetchall()
    
    # Hitung estimasi selesai untuk setiap jadwal
    for j in jadwals_faris:
        jam_mulai = j['jam_tayang_faris']
        if isinstance(jam_mulai, timedelta):
            jam_mulai = (datetime.min + jam_mulai).time()
        durasi = j['durasi_faris']
        jam_selesai = (datetime.combine(datetime.today(), jam_mulai) + timedelta(minutes=int(durasi) + 10)).time()
        j['estimasi_selesai'] = jam_selesai.strftime('%H:%M')
    
    db_faris.close()
    return render_template('pengelola/jadwal_faris.html', jadwals_faris=jadwals_faris)


# 2. TAMBAH JADWAL - FORM
@app.route('/pengelola_faris/jadwal_faris/tambah', methods=['GET', 'POST'])
def pengelola_tambah_jadwal_faris():
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
        batas_faris = datetime.now().date() + timedelta(days=4)
        
        if tgl_faris > batas_faris:
            flash('Jadwal maksimal 4 hari ke depan!', 'danger')
        elif tgl_faris < datetime.now().date():
            flash('Tanggal tidak boleh di masa lalu!', 'danger')
        else:
            cur_faris.execute("SELECT durasi_faris FROM film_faris WHERE id_film_faris=%s", (id_film_faris,))
            film_data = cur_faris.fetchone()
            durasi_film = film_data['durasi_faris'] if film_data else 120
            
            bentrok, pesan_bentrok = cek_bentrok_jadwal(id_teater_faris, tanggal_faris, jam_faris, durasi_film)
            
            if bentrok:
                flash(f'Jadwal bentrok! {pesan_bentrok}', 'danger')
            else:
                cur2_faris = db_faris.cursor()
                cur2_faris.execute(
                    "INSERT INTO jadwal_faris (id_film_faris, id_teater_faris, tanggal_faris, jam_tayang_faris, harga_weekday_faris, harga_weekend_faris, status_faris) VALUES (%s,%s,%s,%s,%s,%s,'tersedia')",
                    (id_film_faris, id_teater_faris, tanggal_faris, jam_faris, harga_weekday_faris, harga_weekend_faris)
                )
                db_faris.commit()
                flash('Jadwal berhasil ditambahkan!', 'success')
                db_faris.close()
                return redirect(url_for('pengelola_jadwal_faris'))
    
    # GET request - tampilkan form
    cur_faris.execute("SELECT * FROM film_faris WHERE status_faris='tayang'")
    films_faris = cur_faris.fetchall()
    cur_faris.execute("SELECT * FROM teater_faris")
    teaters_faris = cur_faris.fetchall()
    db_faris.close()
    
    return render_template('pengelola/tambah_jadwal_faris.html', 
                           films_faris=films_faris, 
                           teaters_faris=teaters_faris)


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
                return redirect(url_for('pengelola_jadwal_faris'))
    
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
    return redirect(url_for('pengelola_jadwal_faris'))

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
def pengelola_laporan_keuangan_faris():
    if session.get('role_faris') != 'pengelola':
        return redirect(url_for('index_faris'))
    
    db_faris = get_db_faris()
    cur_faris = db_faris.cursor(dictionary=True)
    
    # Ambil parameter filter
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    periode = request.args.get('periode', '')
    
    today = datetime.now().date()
    periode_text = ""
    
    # Set filter berdasarkan periode
    if periode == 'hari':
        start_date = today.strftime('%Y-%m-%d')
        end_date = today.strftime('%Y-%m-%d')
        periode_text = f"Hari ini, {today.strftime('%d %B %Y')}"
    elif periode == 'bulan':
        start_date = today.replace(day=1).strftime('%Y-%m-%d')
        end_date = today.strftime('%Y-%m-%d')
        periode_text = f"Bulan {today.strftime('%B %Y')}"
    elif periode == 'tahun':
        start_date = today.replace(month=1, day=1).strftime('%Y-%m-%d')
        end_date = today.strftime('%Y-%m-%d')
        periode_text = f"Tahun {today.strftime('%Y')}"
    elif start_date and end_date:
        # Filter custom tanggal
        start_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
        end_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
        
        if start_obj == end_obj:
            periode_text = f"{start_obj.strftime('%d %B %Y')}"
        elif start_obj.month == end_obj.month and start_obj.year == end_obj.year:
            periode_text = f"{start_obj.strftime('%d')} - {end_obj.strftime('%d %B %Y')}"
        elif start_obj.year == end_obj.year:
            periode_text = f"{start_obj.strftime('%d %B')} - {end_obj.strftime('%d %B %Y')}"
        else:
            periode_text = f"{start_obj.strftime('%d %B %Y')} - {end_obj.strftime('%d %B %Y')}"
    else:
        # Semua data
        periode_text = "Semua Data"
    
    # Query laporan dengan filter tanggal
    query = """
        SELECT p.created_at_faris, p.total_harga_faris, u.nama_faris, 
               f.judul_faris, t.nama_teater_faris
        FROM pemesanan_faris p
        JOIN user_faris u ON p.id_user_faris = u.id_user_faris
        JOIN jadwal_faris j ON p.id_jadwal_faris = j.id_jadwal_faris
        JOIN film_faris f ON j.id_film_faris = f.id_film_faris
        JOIN teater_faris t ON j.id_teater_faris = t.id_teater_faris
        WHERE p.status_bayar_faris = 'lunas'
    """
    params = []
    
    if start_date and end_date:
        query += " AND DATE(p.created_at_faris) BETWEEN %s AND %s"
        params = [start_date, end_date]
    
    query += " ORDER BY p.created_at_faris DESC"
    
    cur_faris.execute(query, params)
    laporan_faris = cur_faris.fetchall()
    
    # Hitung grand total
    grand_total = sum([l['total_harga_faris'] for l in laporan_faris]) if laporan_faris else 0
    
    now_date = datetime.now().strftime('%d %B %Y %H:%M:%S')
    
    db_faris.close()
    
    return render_template('pengelola/laporan_keuangan_faris.html', 
                           laporan_faris=laporan_faris,
                           grand_total=grand_total,
                           start_date=start_date,
                           end_date=end_date,
                           periode_text=periode_text,
                           now_date=now_date)

if __name__ == '__main__':
    app.run(debug=True)