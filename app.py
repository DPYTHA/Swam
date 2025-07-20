from flask import Flask, render_template, request, redirect, session, url_for, jsonify
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import random
import string
from psycopg2 import extras
import psycopg2
import psycopg2.extras
from flask_mail import Mail, Message
from urllib.parse import urlparse
from dotenv import load_dotenv
import os
load_dotenv()

# Initialiser Flask
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "une_cle_par_defaut_si_absente")
CORS(app)

# 🔗 Connexion PostgreSQL via DATABASE_URL
db_url = os.getenv("DATABASE_URL")
if not db_url:
    raise RuntimeError("⚠️ DATABASE_URL manquant dans le fichier .env")

parsed_url = urlparse(db_url)

conn = psycopg2.connect(
    dbname=parsed_url.path[1:],  # enlever le "/" initial
    user=parsed_url.username,
    password=parsed_url.password,
    host=parsed_url.hostname,
    port=parsed_url.port
)

# 📦 Créer l'extension unaccent
cur = conn.cursor()
cur.execute("CREATE EXTENSION IF NOT EXISTS unaccent;")
conn.commit()

# ✉️ Configuration Flask-Mail
app.config.update(
    MAIL_SERVER=os.getenv("MAIL_SERVER"),
    MAIL_PORT=int(os.getenv("MAIL_PORT")),
    MAIL_USE_SSL=os.getenv("MAIL_USE_SSL") == "True",
    MAIL_USERNAME=os.getenv("MAIL_USERNAME"),
    MAIL_PASSWORD=os.getenv("MAIL_PASSWORD"),
    MAIL_DEFAULT_SENDER=(
        os.getenv("MAIL_DEFAULT_SENDER_NAME"),
        os.getenv("MAIL_DEFAULT_SENDER_EMAIL")
    )
)

mail = Mail(app)


# ------------------- FONCTIONS UTILES -------------------

def create_tables():
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            prenom VARCHAR(50),
            nom VARCHAR(50),
            email VARCHAR(100) UNIQUE,
            password TEXT,
            telephone VARCHAR(20),
            role VARCHAR(20),
            disponible BOOLEAN DEFAULT FALSE
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS commandes (
            id SERIAL PRIMARY KEY,
            tracking_code VARCHAR(50) UNIQUE NOT NULL,
            type VARCHAR(30) NOT NULL,
            depart VARCHAR(255) NOT NULL,
            arrivee VARCHAR(255) NOT NULL,
            produits TEXT,
            montant_colis NUMERIC(10,2),
            frais NUMERIC(10,2) NOT NULL,
            montant_total NUMERIC(10,2) NOT NULL,
            distance_km NUMERIC(5,2),
            statut VARCHAR(30) DEFAULT 'en_attente',
            statut_livraison VARCHAR(30) DEFAULT 'en_attente',
            date_commande TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            client_id INTEGER,
            livreur_id INTEGER
        )
    ''')
cur.execute('''
    CREATE TABLE IF NOT EXISTS livraison (
        id SERIAL PRIMARY KEY,
        tracking_code VARCHAR(50) UNIQUE NOT NULL,
        type VARCHAR(30) NOT NULL,
        depart VARCHAR(255) NOT NULL,
        arrivee VARCHAR(255) NOT NULL,
        produits TEXT,
        montant_colis NUMERIC(10,2),
        frais NUMERIC(10,2) NOT NULL,
        montant_total NUMERIC(10,2) NOT NULL,
        distance_km NUMERIC(6,2),
        statut_livraison VARCHAR(30) DEFAULT 'en_attente',
        nom_livreur VARCHAR(50),
        date_creation TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
''')

conn.commit()

create_tables()

def generer_code_suivi():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

def get_commande_by_tracking(code):
    try:
        cur.execute("SELECT * FROM commandes WHERE tracking_code = %s", (code,))
        row = cur.fetchone()
        if row:
            keys = [desc[0] for desc in cur.description]
            return dict(zip(keys, row))
        return None
    except Exception as e:
        print(f"Erreur get_commande_by_tracking : {e}")
        return None

# ------------------- ROUTES DE BASE -------------------
def get_cursor():
    return conn.cursor(cursor_factory=psycopg2.extras.DictCursor)


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/register_login')
def register_login():
    return render_template('register_login.html')

@app.route('/register', methods=['POST'])
def register():
    try:
        prenom = request.form['prenom']
        nom = request.form['nom']
        email = request.form['email']
        password = generate_password_hash(request.form['password'])
        telephone = request.form['telephone']
        role = request.form['role']

        cur.execute('''
            INSERT INTO users (prenom, nom, email, password, telephone, role)
            VALUES (%s, %s, %s, %s, %s, %s)
        ''', (prenom, nom, email, password, telephone, role))
        conn.commit()
        return redirect(url_for('register_login'))
    except Exception as e:
        conn.rollback()
        return f"Erreur : {e}"

@app.route('/login', methods=['POST'])
def login():
    try:
        email = request.form.get('email')
        password = request.form.get('password')

        cur.execute('SELECT * FROM users WHERE email = %s', (email,))
        user = cur.fetchone()

        if user and check_password_hash(user[4], password):
            session['user_id'] = user[0]
            session['role'] = user[6]
            session['prenom'] = user[1]
            return redirect('/admin' if user[6] == 'admin' else '/livreur' if user[6] == 'livreur' else '/commandeVip')
        else:
            return "Email ou mot de passe incorrect", 401
    except Exception as e:
        conn.rollback()
        return f"Erreur lors de la connexion : {str(e)}", 500

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/home')

@app.route('/home')
def home():
    return render_template('home.html')

# ------------------- COMMANDE CLIENT -------------------

@app.route('/commandeVip', methods=['GET', 'POST'])
def commandeVip():
    if request.method == 'POST':
        try:
            data = request.get_json()

            tracking_code = data.get('tracking_code')
            type_commande = data.get('type')
            adresse_depart = data.get('depart')
            adresse_arrivee = data.get('arrivee')
            produits = data.get('produits')
            montant_colis = float(data.get('montant_colis') or 0)
            frais = float(data.get('frais') or 0)
            montant_total = float(data.get('montant_total') or 0)
            distance_km = float(data.get('distance_km') or 0)
            client_id = int(data.get('client_id') or 0)

            if not all([tracking_code, type_commande, adresse_depart, adresse_arrivee, produits]):
                return jsonify({"error": "Champs requis manquants"}), 400

            # ✅ Crée un curseur local ici
            with conn.cursor() as cursor:
                cursor.execute('''
                    INSERT INTO commandes (
                        tracking_code, type, depart, arrivee,
                        produits, montant_colis, frais, montant_total,
                        distance_km, statut, statut_livraison, client_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''', (
                    tracking_code, type_commande, adresse_depart, adresse_arrivee,
                    produits, montant_colis, frais, montant_total,
                    distance_km, 'en_attente', 'en_attente', client_id
                ))
                conn.commit()

            return jsonify({"success": True, "tracking_code": tracking_code})

        except Exception as e:
            conn.rollback()
            return jsonify({"error": "Erreur lors de la commande", "details": str(e)}), 500

    return render_template('commandeVip.html')



@app.route('/suivi/<code>')
def page_suivi(code):
    return render_template("suivi_commande.html", code=code)

cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

@app.route("/api/suivi_commande/<code>", methods=["GET"])
def suivi_commande(code):
    try:
        cursor.execute("""
            SELECT 
                id, tracking_code, type, depart, arrivee, produits, 
                montant_colis, frais, montant_total,
                distance_km, statut, statut_livraison, date_commande,
                client_id, livreur_id
            FROM commandes
            WHERE tracking_code = %s
        """, (code,))
        commande = cursor.fetchone()

        if commande:
            return jsonify({
                "id": commande["id"],
                "tracking_code": commande["tracking_code"],
                "type": commande["type"],
                "depart": commande["depart"],
                "arrivee": commande["arrivee"],
                "produits": commande["produits"],
                "montant_colis": float(commande["montant_colis"]) if commande["montant_colis"] else 0.0,
                "frais": float(commande["frais"]) if commande["frais"] else 0.0,
                "montant_total": float(commande["montant_total"]) if commande["montant_total"] else 0.0,
                "distance_km": float(commande["distance_km"]) if commande["distance_km"] else None,
                "statut": commande["statut"],
                "statut_livraison": commande["statut_livraison"],
                "date_commande": commande["date_commande"].strftime('%Y-%m-%d %H:%M:%S') if commande["date_commande"] else None,
                "client_id": commande["client_id"],
                "livreur_id": commande["livreur_id"]
            })
        else:
            return jsonify({"error": "Commande non trouvée"}), 404

    except Exception as e:
        return jsonify({"error": "Erreur serveur", "details": str(e)}), 500


# ------------------- LIVREUR -------------------

@app.route("/livreur", methods=["GET", "POST"])
def livreur_dashboard():
    commande = None
    message = None
    if request.method == "POST":
        code = request.form.get("tracking_code")
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor:
            cursor.execute("SELECT * FROM commandes WHERE tracking_code = %s", (code,))
            commande = cursor.fetchone()
            if not commande:
                message = "Commande introuvable."
    return render_template("livreur.html", commande=commande, message=message)

from flask import request, jsonify

@app.route("/api/maj_statut/<code>", methods=["POST"])
def maj_statut_livraison(code):
    try:
        data = request.get_json()
        nouveau_statut = data.get("statut")

        if nouveau_statut not in ["en_attente", "en_cours", "livree"]:
            return jsonify({"error": "Statut invalide"}), 400

        cursor = conn.cursor()
        cursor.execute(
            "UPDATE commandes SET statut_livraison = %s WHERE tracking_code = %s",
            (nouveau_statut, code)
        )
        conn.commit()
        return jsonify({"message": "Statut mis à jour avec succès"})
    
    except Exception as e:
        print("Erreur mise à jour:", e)
        conn.rollback()
        return jsonify({"error": "Erreur serveur"}), 500
    

@app.route('/api/users')
def get_users():
    cur.execute("SELECT id, prenom, nom, email, telephone, role, disponible FROM users ORDER BY id")
    users = [dict(zip([desc[0] for desc in cur.description], row)) for row in cur.fetchall()]
    return jsonify(users)


# ------------------- ADMIN -------------------

@app.route('/admin')
def admin():
    return render_template('admin.html')

@app.route('/livraison')
def livraison():
    return render_template('livraison.html')

@app.route('/api/admin/commandes')
def commandes_admin():
    try:
        cur.execute('SELECT * FROM commandes ORDER BY date_commande DESC')
        commandes = cur.fetchall()
        colonnes = [desc[0] for desc in cur.description]
        commandes_dict = [dict(zip(colonnes, row)) for row in commandes]
        return jsonify(commandes_dict)  # ✅ JSON, pas render_template
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/update_commande', methods=['POST'])
def update_commande():
    data = request.get_json()
    code = data['code']
    type_ = data['type']
    depart = data['depart']
    arrivee = data['arrivee']

    cur = conn.cursor()
    cur.execute('''
        UPDATE commandes
        SET type = %s, depart = %s, arrivee = %s
        WHERE tracking_code = %s
    ''', (type_, depart, arrivee, code))
    conn.commit()
    return jsonify({'message': 'Commande mise à jour.'})


@app.route('/api/livreur/disponibilite', methods=['POST'])
def maj_disponibilite():
    try:
        data = request.get_json()
        disponible = data.get('disponible')

        # Remplace ceci par l’ID réel du livreur, selon ta session/login
        livreur_id = session.get('user_id')  # À ajuster selon ton système

        cur.execute("UPDATE users SET disponible = %s WHERE id = %s", (disponible, livreur_id))
        conn.commit()
        return jsonify({"message": "Disponibilité mise à jour."})
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/update_statut', methods=['POST'])
def update_statut_admin():
    data = request.get_json()
    code = data.get('code')
    statut = data.get('statut')
    cur.execute("UPDATE commandes SET statut_livraison = %s WHERE tracking_code = %s", (statut, code))
    conn.commit()
    return jsonify({'message': f"Statut mis à jour pour {code}"})

@app.route('/api/admin/delete_commande', methods=['POST'])
def delete_commande():
    data = request.get_json()
    code = data.get('code')
    cur.execute("DELETE FROM commandes WHERE tracking_code = %s", (code,))
    conn.commit()
    return jsonify({'message': f"Commande {code} supprimée"})


@app.route('/paiement')
def paiement():
    return render_template("paiement.html")


@app.route('/api/livraisons')
def get_livraisons():
    cur.execute("SELECT * FROM livraison")
    livraisons = cur.fetchall()
    return jsonify([dict(l) for l in livraisons])

@app.route("/api/livraisons", methods=["GET", "POST"])
def api_livraisons():
   
    global cur, conn
    
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if request.method == "GET":
        cur.execute("SELECT * FROM livraison ORDER BY tracking_code DESC")
        data = cur.fetchall()
       
        return jsonify(data)

    if request.method == "POST":
        livraisons = request.json.get("livraisons", [])
        for row in livraisons:
            cur.execute("""
                INSERT INTO livraison (tracking_code, type, depart, arrivee, produits, montant_colis, frais, montant_total, distance_km, statut_livraison, nom_livreur)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tracking_code) DO UPDATE SET
                    type = EXCLUDED.type,
                    depart = EXCLUDED.depart,
                    arrivee = EXCLUDED.arrivee,
                    produits = EXCLUDED.produits,
                    montant_colis = EXCLUDED.montant_colis,
                    frais = EXCLUDED.frais,
                    montant_total = EXCLUDED.montant_total,
                    distance_km = EXCLUDED.distance_km,
                    statut_livraison = EXCLUDED.statut_livraison,
                    nom_livreur = EXCLUDED.nom_livreur
            """, (
                row.get("tracking_code"),
                row.get("type"),
                row.get("depart"),
                row.get("arrivee"),
                row.get("produits"),
                row.get("montant_colis"),
                row.get("frais"),
                row.get("montant_total"),
                row.get("distance_km"),
                row.get("statut_livraison"),
                row.get("nom_livreur")
            ))
        conn.commit()
       
        return jsonify({"message": "Données enregistrées"}), 200


# ------------------- AUTRES -------------------

@app.route('/confirmation')
def confirmation():
    return render_template("confirmation.html")

@app.route('/suivi_commande')
def suivi_commande1():
    return render_template("suivi_commande.html")

@app.route('/deconnexion')
def deconnexion():
    session.clear()  # Supprime toutes les données de session
    return redirect(url_for('home')) 


