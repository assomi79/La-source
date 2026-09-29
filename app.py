#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GESTION ECOLE PRIVEE - Primaire (mensuel) + Secondaire (trimestriel) - V2
Espace Etablissement (staff) + Espace Parent
Parent login : NOM eleve (utilisateur) + matricule (mot de passe initial, modifiable)
Notifications absence : boutons WhatsApp / SMS vers numero tuteur
Lancement : python app.py -> http://localhost:5000 (admin/admin123) | Espace parent : /parent/login
"""
import sqlite3, os, datetime, csv, io, re
from urllib.parse import quote
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, g, send_file, jsonify, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("ECOLE_DB", os.path.join(BASE_DIR, "ecole.db"))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
ALLOWED_EXT = {"pdf","doc","docx","xls","xlsx","ppt","pptx","txt","jpg","jpeg","png","gif","webp"}
MAX_MB = 25

app = Flask(__name__)
app.secret_key = "ecole-privee-cle-2025-changez-moi"
app.config["MAX_CONTENT_LENGTH"] = MAX_MB * 1024 * 1024

MOIS_PRIMAIRE = ["Septembre","Octobre","Novembre","Décembre","Janvier","Février","Mars","Avril","Mai","Juin"]
TRIMESTRES = ["Trimestre 1","Trimestre 2","Trimestre 3"]
CYCLES = ["Primaire","Secondaire"]
CLASSES_PRIMAIRE = ["CP1","CP2","CE1","CE2","CM1","CM2"]
CLASSES_SECONDAIRE = ["6ème","5ème","4ème","3ème","2nde A","2nde C","1ère A","1ère C","1ère D","Tle A","Tle C","Tle D"]
RUBRIQUES = ["Inscription","Scolarité T1","Scolarité T2","Scolarité T3","Cantine","Transport","Uniforme","Autre"]
MODES_PAIEMENT = ["Espèces","Mobile Money","Virement","Chèque"]

# ---------- DB ----------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db: db.close()

def init_db():
    db = sqlite3.connect(DB_PATH)
    c = db.cursor()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT, nom TEXT, username TEXT UNIQUE, password TEXT, role TEXT);
    CREATE TABLE IF NOT EXISTS annees(id INTEGER PRIMARY KEY AUTOINCREMENT, libelle TEXT UNIQUE, active INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS classes(id INTEGER PRIMARY KEY AUTOINCREMENT, nom TEXT UNIQUE, cycle TEXT, niveau TEXT, titulaire TEXT DEFAULT '', effectif_max INTEGER DEFAULT 60, frais_inscription INTEGER DEFAULT 0, frais_scolarite INTEGER DEFAULT 0, frais_cantine INTEGER DEFAULT 0, frais_transport INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS eleves(id INTEGER PRIMARY KEY AUTOINCREMENT, matricule TEXT UNIQUE, nom TEXT, prenoms TEXT, sexe TEXT, date_naissance TEXT, lieu_naissance TEXT DEFAULT '', cycle TEXT, classe_id INTEGER, tuteur TEXT, telephone_tuteur TEXT, adresse TEXT DEFAULT '', statut TEXT DEFAULT 'Inscrit', annee_id INTEGER, date_inscription TEXT);
    CREATE TABLE IF NOT EXISTS matieres(id INTEGER PRIMARY KEY AUTOINCREMENT, nom TEXT, code TEXT, cycle TEXT);
    CREATE TABLE IF NOT EXISTS classe_matiere(id INTEGER PRIMARY KEY AUTOINCREMENT, classe_id INTEGER, matiere_id INTEGER, coefficient INTEGER DEFAULT 1, UNIQUE(classe_id, matiere_id));
    CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY AUTOINCREMENT, eleve_id INTEGER, matiere_id INTEGER, classe_id INTEGER, periode_type TEXT, periode TEXT, note REAL, coef INTEGER DEFAULT 1, date_saisie TEXT, annee_id INTEGER);
    CREATE TABLE IF NOT EXISTS personnel(id INTEGER PRIMARY KEY AUTOINCREMENT, matricule TEXT UNIQUE, nom TEXT, prenoms TEXT, fonction TEXT, telephone TEXT, email TEXT DEFAULT '', matiere TEXT DEFAULT '', salaire_base INTEGER DEFAULT 0, date_embauche TEXT, statut TEXT DEFAULT 'Actif');
    CREATE TABLE IF NOT EXISTS presences(id INTEGER PRIMARY KEY AUTOINCREMENT, eleve_id INTEGER, classe_id INTEGER, date TEXT, statut TEXT, motif TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS disciplines(id INTEGER PRIMARY KEY AUTOINCREMENT, eleve_id INTEGER, date TEXT, type TEXT, motif TEXT, sanction TEXT DEFAULT '', auteur TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS paiements(id INTEGER PRIMARY KEY AUTOINCREMENT, eleve_id INTEGER, date TEXT, rubrique TEXT, montant INTEGER, mode TEXT, recu_no TEXT UNIQUE, auteur TEXT, annee_id INTEGER);
    CREATE TABLE IF NOT EXISTS depenses(id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, categorie TEXT, libelle TEXT, montant INTEGER, beneficiaire TEXT DEFAULT '', auteur TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS parents(id INTEGER PRIMARY KEY AUTOINCREMENT, eleve_id INTEGER UNIQUE, password TEXT, telephone TEXT DEFAULT '', must_change INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS demandes(id INTEGER PRIMARY KEY AUTOINCREMENT, nom TEXT, prenoms TEXT, sexe TEXT, date_naissance TEXT, classe_souhaitee TEXT, tuteur TEXT, telephone_tuteur TEXT, adresse TEXT DEFAULT '', statut TEXT DEFAULT 'En attente', date_demande TEXT, annee_id INTEGER);
    CREATE TABLE IF NOT EXISTS ressources(id INTEGER PRIMARY KEY AUTOINCREMENT, classe_id INTEGER, matiere TEXT, titre TEXT, description TEXT DEFAULT '', lien TEXT DEFAULT '', date_pub TEXT);
    CREATE TABLE IF NOT EXISTS devoirs(id INTEGER PRIMARY KEY AUTOINCREMENT, classe_id INTEGER, matiere TEXT, titre TEXT, consigne TEXT, date_donnee TEXT, date_limite TEXT);
    CREATE TABLE IF NOT EXISTS cours(id INTEGER PRIMARY KEY AUTOINCREMENT, classe_id INTEGER, matiere TEXT, titre TEXT, contenu TEXT, date_pub TEXT);
    CREATE TABLE IF NOT EXISTS notif_log(id INTEGER PRIMARY KEY AUTOINCREMENT, eleve_id INTEGER, date TEXT, canal TEXT, message TEXT, auteur TEXT);
    CREATE TABLE IF NOT EXISTS fichiers(id INTEGER PRIMARY KEY AUTOINCREMENT, classe_id INTEGER, matiere TEXT DEFAULT '', titre TEXT, type_doc TEXT DEFAULT 'Cours', filename TEXT, original TEXT, taille INTEGER DEFAULT 0, date_pub TEXT, auteur TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS settings(cle TEXT PRIMARY KEY, valeur TEXT);
    """)
    # Réglages école (multi-écoles : 1 base par école)
    c.execute("INSERT OR IGNORE INTO settings(cle,valeur) VALUES('ecole_nom','Mon École Privée')")
    c.execute("INSERT OR IGNORE INTO settings(cle,valeur) VALUES('type_etab','Mixte')")
    # type_etab = Primaire seul | Secondaire seul | Mixte (Primaire+Secondaire)
    # Migration douce : colonne cours sur presences (appel par cours)
    try:
        cols = [r[1] for r in c.execute("PRAGMA table_info(presences)").fetchall()]
        if "cours" not in cols:
            c.execute("ALTER TABLE presences ADD COLUMN cours TEXT DEFAULT ''")
    except: pass
    # Année active
    if not c.execute("SELECT * FROM annees").fetchone():
        lib = f"{datetime.date.today().year}-{datetime.date.today().year+1}"
        c.execute("INSERT INTO annees(libelle, active) VALUES(?,1)", (lib,))
    # Admin
    if not c.execute("SELECT * FROM users WHERE username='admin'").fetchone():
        c.execute("INSERT INTO users(nom,username,password,role) VALUES(?,?,?,?)",
                  ("Directeur", "admin", generate_password_hash("admin123"), "Directeur"))
    # Classes par défaut
    if not c.execute("SELECT COUNT(*) FROM classes").fetchone()[0]:
        for n in CLASSES_PRIMAIRE:
            c.execute("INSERT INTO classes(nom,cycle,niveau,frais_inscription,frais_scolarite) VALUES(?,?,?,?,?)",
                      (n,"Primaire",n,10000,75000))
        for n in CLASSES_SECONDAIRE:
            c.execute("INSERT INTO classes(nom,cycle,niveau,frais_inscription,frais_scolarite) VALUES(?,?,?,?,?)",
                      (n,"Secondaire",n,15000,120000))
    # Matières par défaut
    if not c.execute("SELECT COUNT(*) FROM matieres").fetchone()[0]:
        prim = ["Français","Mathématiques","Éveil au Milieu","Histoire-Géo","Sciences","Lecture","Écriture","Poésie/Chant","EPS","EDHC","Anglais (CM)"]
        sec = ["Français","Mathématiques","Histoire-Géo","SVT","Physique-Chimie","Anglais","Espagnol/Allemand","Philosophie","EPS","EDHC","Informatique"]
        for m in prim: c.execute("INSERT INTO matieres(nom,code,cycle) VALUES(?,?,?)",(m,m[:3].upper(),"Primaire"))
        for m in sec: c.execute("INSERT INTO matieres(nom,code,cycle) VALUES(?,?,?)",(m,m[:3].upper(),"Secondaire"))
    db.commit(); db.close()

def ensure_schema():
    """Migration auto pour bases V1/V2 : crée les tables manquantes sans perdre les données."""
    try:
        db = get_db()
        db.executescript("""
        CREATE TABLE IF NOT EXISTS parents(id INTEGER PRIMARY KEY AUTOINCREMENT, eleve_id INTEGER UNIQUE, password TEXT, telephone TEXT DEFAULT '', must_change INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS demandes(id INTEGER PRIMARY KEY AUTOINCREMENT, nom TEXT, prenoms TEXT, sexe TEXT, date_naissance TEXT, classe_souhaitee TEXT, tuteur TEXT, telephone_tuteur TEXT, adresse TEXT DEFAULT '', statut TEXT DEFAULT 'En attente', date_demande TEXT, annee_id INTEGER);
        CREATE TABLE IF NOT EXISTS ressources(id INTEGER PRIMARY KEY AUTOINCREMENT, classe_id INTEGER, matiere TEXT, titre TEXT, description TEXT DEFAULT '', lien TEXT DEFAULT '', date_pub TEXT);
        CREATE TABLE IF NOT EXISTS devoirs(id INTEGER PRIMARY KEY AUTOINCREMENT, classe_id INTEGER, matiere TEXT, titre TEXT, consigne TEXT, date_donnee TEXT, date_limite TEXT);
        CREATE TABLE IF NOT EXISTS cours(id INTEGER PRIMARY KEY AUTOINCREMENT, classe_id INTEGER, matiere TEXT, titre TEXT, contenu TEXT, date_pub TEXT);
        CREATE TABLE IF NOT EXISTS notif_log(id INTEGER PRIMARY KEY AUTOINCREMENT, eleve_id INTEGER, date TEXT, canal TEXT, message TEXT, auteur TEXT);
        CREATE TABLE IF NOT EXISTS settings(cle TEXT PRIMARY KEY, valeur TEXT);
        CREATE TABLE IF NOT EXISTS fichiers(id INTEGER PRIMARY KEY AUTOINCREMENT, classe_id INTEGER, matiere TEXT DEFAULT '', titre TEXT, type_doc TEXT DEFAULT 'Cours', filename TEXT, original TEXT, taille INTEGER DEFAULT 0, date_pub TEXT, auteur TEXT DEFAULT '');
        """)
        try:
            cols = [r[1] for r in db.execute("PRAGMA table_info(presences)").fetchall()]
            if "cours" not in cols:
                db.execute("ALTER TABLE presences ADD COLUMN cours TEXT DEFAULT ''")
        except: pass
        db.execute("INSERT OR IGNORE INTO settings(cle,valeur) VALUES('ecole_nom','Mon École Privée')")
        db.execute("INSERT OR IGNORE INTO settings(cle,valeur) VALUES('type_etab','Mixte')")
        db.commit()
    except: pass

@app.before_request
def _migrate():
    ensure_schema()

def annee_active():
    db = get_db()
    a = db.execute("SELECT * FROM annees WHERE active=1").fetchone()
    if not a:
        a = db.execute("SELECT * FROM annees ORDER BY id DESC").fetchone()
    return a

def nouveau_matricule(prefix="E"):
    db = get_db()
    n = db.execute("SELECT COUNT(*) c FROM eleves").fetchone()["c"] + 1
    year = datetime.date.today().year
    return f"{prefix}-{year}-{n:04d}"

def nouveau_recu():
    db = get_db()
    n = db.execute("SELECT COUNT(*) c FROM paiements").fetchone()["c"] + 1
    return f"REC-{datetime.date.today().year}-{n:05d}"

# ---------- Auth ----------
def login_required(f):
    @wraps(f)
    def d(*a, **kw):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*a, **kw)
    return d

def role_required(*roles):
    def deco(f):
        @wraps(f)
        def d(*a, **kw):
            if "user_id" not in session:
                return redirect(url_for("login"))
            if session.get("role") not in roles:
                flash("Accès réservé : " + ", ".join(roles) + ".", "danger")
                return redirect(url_for("dashboard"))
            return f(*a, **kw)
        return d
    return deco

GESTION_ACCES = ("Directeur", "Secrétaire")  # qui peut créer/modifier/annuler les accès + rôles

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        u = request.form.get("username","").strip()
        p = request.form.get("password","")
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username=?", (u,)).fetchone()
        if user and check_password_hash(user["password"], p):
            session["user_id"]=user["id"]; session["nom"]=user["nom"]; session["role"]=user["role"]; session["username"]=user["username"]
            return redirect(url_for("dashboard"))
        flash("Identifiants incorrects.", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ---------- Dashboard ----------
@app.route("/")
@login_required
def dashboard():
    db = get_db(); an = annee_active()
    aid = an["id"] if an else None
    stats = {}
    stats["total"] = db.execute("SELECT COUNT(*) c FROM eleves WHERE annee_id=?", (aid,)).fetchone()["c"] if aid else 0
    stats["primaire"] = db.execute("SELECT COUNT(*) c FROM eleves WHERE cycle='Primaire' AND annee_id=?", (aid,)).fetchone()["c"] if aid else 0
    stats["secondaire"] = db.execute("SELECT COUNT(*) c FROM eleves WHERE cycle='Secondaire' AND annee_id=?", (aid,)).fetchone()["c"] if aid else 0
    stats["filles"] = db.execute("SELECT COUNT(*) c FROM eleves WHERE sexe='F' AND annee_id=?", (aid,)).fetchone()["c"] if aid else 0
    stats["garcons"] = stats["total"] - stats["filles"]
    stats["personnel"] = db.execute("SELECT COUNT(*) c FROM personnel WHERE statut='Actif'").fetchone()["c"]
    stats["abs_today"] = db.execute("SELECT COUNT(*) c FROM presences WHERE date=? AND statut='Absent'", (datetime.date.today().isoformat(),)).fetchone()["c"]
    stats["recettes"] = db.execute("SELECT COALESCE(SUM(montant),0) s FROM paiements WHERE annee_id=?", (aid,)).fetchone()["s"] if aid else 0
    stats["depenses"] = db.execute("SELECT COALESCE(SUM(montant),0) s FROM depenses").fetchone()["s"]
    # impayés estimés
    classes = db.execute("SELECT * FROM classes").fetchall()
    frais_map = {cl["id"]: (cl["frais_inscription"] or 0)+(cl["frais_scolarite"] or 0) for cl in classes}
    eleves = db.execute("SELECT id, classe_id FROM eleves WHERE annee_id=?", (aid,)).fetchall() if aid else []
    du_total = sum(frais_map.get(e["classe_id"],0) for e in eleves)
    stats["attendus"] = du_total
    stats["impayes"] = max(0, du_total - (stats["recettes"] or 0))
    # effectifs par classe (filtrés selon type d'établissement)
    _t = get_setting("type_etab", "Mixte")
    _cycle_filtre = "Primaire" if _t == "Primaire seul" else ("Secondaire" if _t == "Secondaire seul" else "")
    if _cycle_filtre:
        eff = db.execute("""SELECT cl.nom, cl.cycle, COUNT(e.id) n FROM classes cl LEFT JOIN eleves e ON e.classe_id=cl.id AND e.annee_id=? WHERE cl.cycle=? GROUP BY cl.id ORDER BY cl.nom""",(aid,_cycle_filtre)).fetchall() if aid else []
        recents = db.execute("""SELECT e.*, cl.nom classe_nom FROM eleves e LEFT JOIN classes cl ON cl.id=e.classe_id WHERE e.annee_id=? AND e.cycle=? ORDER BY e.id DESC LIMIT 8""",(aid,_cycle_filtre)).fetchall() if aid else []
    else:
        eff = db.execute("""SELECT cl.nom, cl.cycle, COUNT(e.id) n FROM classes cl LEFT JOIN eleves e ON e.classe_id=cl.id AND e.annee_id=? GROUP BY cl.id ORDER BY cl.cycle, cl.nom""",(aid,)).fetchall() if aid else []
        # dernières inscriptions
        recents = db.execute("""SELECT e.*, cl.nom classe_nom FROM eleves e LEFT JOIN classes cl ON cl.id=e.classe_id WHERE e.annee_id=? ORDER BY e.id DESC LIMIT 8""",(aid,)).fetchall() if aid else []
    return render_template("dashboard.html", stats=stats, eff=eff, recents=recents, annee=an)

# ---------- Scolarité : Élèves ----------
@app.route("/eleves")
@login_required
def eleves():
    db = get_db(); an = annee_active()
    q = request.args.get("q","").strip()
    cycle = request.args.get("cycle","")
    classe_id = request.args.get("classe_id","")
    # Si type Primaire/Secondaire seul et aucun filtre choisi : force le filtre pour ne pas voir l'autre cycle
    _t = get_setting("type_etab", "Mixte")
    if not cycle:
        if _t == "Primaire seul": cycle = "Primaire"
        elif _t == "Secondaire seul": cycle = "Secondaire"
    sql = """SELECT e.*, cl.nom classe_nom FROM eleves e LEFT JOIN classes cl ON cl.id=e.classe_id WHERE e.annee_id=?"""
    params = [an["id"]]
    if q: sql += " AND (e.nom LIKE ? OR e.prenoms LIKE ? OR e.matricule LIKE ?)"; params += [f"%{q}%"]*3
    if cycle: sql += " AND e.cycle=?"; params.append(cycle)
    if classe_id: sql += " AND e.classe_id=?"; params.append(classe_id)
    sql += " ORDER BY e.nom, e.prenoms"
    rows = db.execute(sql, params).fetchall()
    classes = classes_filtrees()
    return render_template("eleves.html", eleves=rows, classes=classes, q=q, cycle=cycle, classe_id=classe_id, annee=an)

@app.route("/eleves/ajouter", methods=["GET","POST"])
@role_required("Directeur","Secrétaire")
def eleve_ajouter():
    db = get_db(); an = annee_active()
    classes = classes_filtrees()
    if request.method == "POST":
        f = request.form
        classe = db.execute("SELECT * FROM classes WHERE id=?", (f.get("classe_id"),)).fetchone()
        cycle = classe["cycle"] if classe else f.get("cycle","Primaire")
        mat = nouveau_matricule()
        db.execute("""INSERT INTO eleves(matricule,nom,prenoms,sexe,date_naissance,lieu_naissance,cycle,classe_id,tuteur,telephone_tuteur,adresse,statut,annee_id,date_inscription)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (mat, f.get("nom","").upper().strip(), f.get("prenoms","").strip(), f.get("sexe","M"),
                    f.get("date_naissance",""), f.get("lieu_naissance",""), cycle, f.get("classe_id"),
                    f.get("tuteur",""), f.get("telephone_tuteur",""), f.get("adresse",""), "Inscrit", an["id"], datetime.date.today().isoformat()))
        db.commit()
        flash(f"Élève inscrit : {mat}", "success")
        return redirect(url_for("eleves"))
    return render_template("eleve_form.html", classes=classes, eleve=None)

@app.route("/eleves/<int:eid>")
@login_required
def eleve_fiche(eid):
    db = get_db()
    e = db.execute("SELECT e.*, cl.nom classe_nom, cl.frais_inscription, cl.frais_scolarite FROM eleves e LEFT JOIN classes cl ON cl.id=e.classe_id WHERE e.id=?", (eid,)).fetchone()
    if not e: return redirect(url_for("eleves"))
    notes = db.execute("SELECT n.*, m.nom mat FROM notes n JOIN matieres m ON m.id=n.matiere_id WHERE n.eleve_id=? ORDER BY n.periode, mat", (eid,)).fetchall()
    pays = db.execute("SELECT * FROM paiements WHERE eleve_id=? ORDER BY date DESC", (eid,)).fetchall()
    total_du = (e["frais_inscription"] or 0) + (e["frais_scolarite"] or 0)
    total_paye = sum(p["montant"] for p in pays)
    pres = db.execute("SELECT * FROM presences WHERE eleve_id=? ORDER BY date DESC LIMIT 20", (eid,)).fetchall()
    disc = db.execute("SELECT * FROM disciplines WHERE eleve_id=? ORDER BY date DESC", (eid,)).fetchall()
    return render_template("eleve_fiche.html", e=e, notes=notes, paiements=pays, total_du=total_du, total_paye=total_paye, reste=total_du-total_paye, presences=pres, disciplines=disc)

@app.route("/eleves/<int:eid>/modifier", methods=["GET","POST"])
@role_required("Directeur","Secrétaire")
def eleve_modifier(eid):
    db = get_db()
    e = db.execute("SELECT * FROM eleves WHERE id=?", (eid,)).fetchone()
    classes = classes_filtrees()
    if not e: return redirect(url_for("eleves"))
    if request.method == "POST":
        f = request.form
        classe = db.execute("SELECT * FROM classes WHERE id=?", (f.get("classe_id"),)).fetchone()
        cycle = classe["cycle"] if classe else e["cycle"]
        db.execute("""UPDATE eleves SET nom=?,prenoms=?,sexe=?,date_naissance=?,lieu_naissance=?,cycle=?,classe_id=?,tuteur=?,telephone_tuteur=?,adresse=?,statut=? WHERE id=?""",
                   (f.get("nom","").upper().strip(), f.get("prenoms","").strip(), f.get("sexe","M"), f.get("date_naissance",""), f.get("lieu_naissance",""), cycle, f.get("classe_id"), f.get("tuteur",""), f.get("telephone_tuteur",""), f.get("adresse",""), f.get("statut","Inscrit"), eid))
        db.commit(); flash("Fiche élève mise à jour.", "success")
        return redirect(url_for("eleve_fiche", eid=eid))
    return render_template("eleve_form.html", classes=classes, eleve=e)

@app.route("/eleves/<int:eid>/supprimer", methods=["POST"])
@login_required
def eleve_supprimer(eid):
    if session.get("role") not in ("Directeur","Secrétaire"):
        flash("Action réservée à la direction.", "danger"); return redirect(url_for("eleves"))
    db = get_db()
    db.execute("DELETE FROM notes WHERE eleve_id=?", (eid,))
    db.execute("DELETE FROM paiements WHERE eleve_id=?", (eid,))
    db.execute("DELETE FROM presences WHERE eleve_id=?", (eid,))
    db.execute("DELETE FROM disciplines WHERE eleve_id=?", (eid,))
    db.execute("DELETE FROM eleves WHERE id=?", (eid,))
    db.commit(); flash("Élève supprimé.", "warning")
    return redirect(url_for("eleves"))

# ---------- Classes & Matières ----------
@app.route("/classes", methods=["GET","POST"])
@login_required
def classes():
    db = get_db()
    if request.method == "POST":
        f = request.form
        try:
            db.execute("""INSERT INTO classes(nom,cycle,niveau,titulaire,effectif_max,frais_inscription,frais_scolarite,frais_cantine,frais_transport)
                          VALUES(?,?,?,?,?,?,?,?,?)""",
                       (f.get("nom","").strip(), f.get("cycle","Primaire"), f.get("niveau",""), f.get("titulaire",""),
                        int(f.get("effectif_max") or 60), int(f.get("frais_inscription") or 0), int(f.get("frais_scolarite") or 0),
                        int(f.get("frais_cantine") or 0), int(f.get("frais_transport") or 0)))
            db.commit(); flash("Classe ajoutée.", "success")
        except sqlite3.IntegrityError:
            flash("Cette classe existe déjà.", "danger")
        return redirect(url_for("classes"))
    rows = db.execute("""SELECT cl.*, COUNT(e.id) eff FROM classes cl LEFT JOIN eleves e ON e.classe_id=cl.id GROUP BY cl.id ORDER BY cl.cycle, cl.nom""").fetchall()
    # Filtrage affichage selon type d'établissement (les autres restent en base, masquées). ?voir_tout=1 pour nettoyer.
    _t = get_setting("type_etab", "Mixte")
    voir_tout = request.args.get("voir_tout") == "1"
    if not voir_tout:
        if _t == "Primaire seul": rows = [r for r in rows if r["cycle"] == "Primaire"]
        elif _t == "Secondaire seul": rows = [r for r in rows if r["cycle"] == "Secondaire"]
    return render_template("classes.html", classes=rows, voir_tout=voir_tout)

@app.route("/classes/<int:cid>/supprimer", methods=["POST"])
@login_required
def classe_supprimer(cid):
    db = get_db()
    n = db.execute("SELECT COUNT(*) c FROM eleves WHERE classe_id=?", (cid,)).fetchone()["c"]
    if n > 0: flash("Classe non vide : réaffectez les élèves d'abord.", "danger")
    else: db.execute("DELETE FROM classes WHERE id=?", (cid,)); db.commit(); flash("Classe supprimée.", "warning")
    return redirect(url_for("classes"))

@app.route("/matieres", methods=["GET","POST"])
@login_required
def matieres():
    db = get_db()
    if request.method == "POST":
        f = request.form
        db.execute("INSERT INTO matieres(nom,code,cycle) VALUES(?,?,?)", (f.get("nom","").strip(), f.get("code","").strip().upper(), f.get("cycle","Primaire")))
        db.commit(); flash("Matière ajoutée.", "success")
        return redirect(url_for("matieres"))
    rows = db.execute("SELECT * FROM matieres ORDER BY cycle, nom").fetchall()
    classes = classes_filtrees()
    # Filtrage matières selon type
    _t2 = get_setting("type_etab", "Mixte")
    if _t2 == "Primaire seul": rows = [r for r in rows if r["cycle"] == "Primaire"]
    elif _t2 == "Secondaire seul": rows = [r for r in rows if r["cycle"] == "Secondaire"]
    # coefficients par classe
    coeffs = db.execute("""SELECT cm.*, cl.nom classe_nom, m.nom mat_nom FROM classe_matiere cm JOIN classes cl ON cl.id=cm.classe_id JOIN matieres m ON m.id=cm.matiere_id ORDER BY cl.nom, m.nom""").fetchall()
    return render_template("matieres.html", matieres=rows, classes=classes, coeffs=coeffs)

@app.route("/matieres/coef", methods=["POST"])
@login_required
def matiere_coef():
    db = get_db()
    db.execute("INSERT OR REPLACE INTO classe_matiere(classe_id, matiere_id, coefficient) VALUES(?,?,?)",
               (request.form.get("classe_id"), request.form.get("matiere_id"), int(request.form.get("coefficient") or 1)))
    db.commit(); flash("Coefficient enregistré.", "success")
    return redirect(url_for("matieres"))

# ---------- Pédagogie : Notes & Bulletins ----------
@app.route("/notes", methods=["GET","POST"])
@role_required("Directeur","Secrétaire","Enseignant")
def notes():
    db = get_db(); an = annee_active()
    classes = classes_filtrees()
    _tm = get_setting("type_etab", "Mixte")
    if _tm == "Primaire seul": matieres = db.execute("SELECT * FROM matieres WHERE cycle='Primaire' ORDER BY nom").fetchall()
    elif _tm == "Secondaire seul": matieres = db.execute("SELECT * FROM matieres WHERE cycle='Secondaire' ORDER BY nom").fetchall()
    else: matieres = db.execute("SELECT * FROM matieres ORDER BY nom").fetchall()
    classe_id = request.values.get("classe_id","")
    periode = request.values.get("periode","")
    matiere_id = request.values.get("matiere_id","")
    lignes = []
    if classe_id and periode:
        lignes = db.execute("""SELECT e.id, e.matricule, e.nom, e.prenoms, n.note, n.id nid, n.coef FROM eleves e
                               LEFT JOIN notes n ON n.eleve_id=e.id AND n.classe_id=? AND n.periode=? AND n.matiere_id=?
                               WHERE e.classe_id=? ORDER BY e.nom""",
                            (classe_id, periode, matiere_id or 0, classe_id)).fetchall() if matiere_id else \
                 db.execute("SELECT id, matricule, nom, prenoms, NULL note, NULL nid, 1 coef FROM eleves WHERE classe_id=? ORDER BY nom", (classe_id,)).fetchall()
    if request.method == "POST" and "sauvegarder" in request.form:
        # sauvegarde multiple
        ids = request.form.getlist("eleve_id")
        vals = request.form.getlist("note")
        coefs = request.form.getlist("coef")
        for eid, v, cf in zip(ids, vals, coefs):
            if v == "" or v is None: continue
            try: note = float(str(v).replace(",","."))
            except: continue
            if not (0 <= note <= 20): continue
            ex = db.execute("SELECT id FROM notes WHERE eleve_id=? AND classe_id=? AND matiere_id=? AND periode=? AND annee_id=?",
                            (eid, classe_id, matiere_id, periode, an["id"])).fetchone()
            if ex: db.execute("UPDATE notes SET note=?, coef=? WHERE id=?", (note, int(cf or 1), ex["id"]))
            else: db.execute("INSERT INTO notes(eleve_id,matiere_id,classe_id,periode_type,periode,note,coef,date_saisie,annee_id) VALUES(?,?,?,?,?,?,?,?,?)",
                             (eid, matiere_id, classe_id, "Mensuel" if db.execute("SELECT cycle FROM classes WHERE id=?",(classe_id,)).fetchone()["cycle"]=="Primaire" else "Trimestre", periode, note, int(cf or 1), datetime.date.today().isoformat(), an["id"]))
        db.commit(); flash(f"Notes enregistrées ({periode}).", "success")
        return redirect(url_for("notes", classe_id=classe_id, periode=periode, matiere_id=matiere_id))
    return render_template("notes.html", classes=classes, matieres=matieres, lignes=lignes, classe_id=classe_id, periode=periode, matiere_id=matiere_id, mois=MOIS_PRIMAIRE, trimestres=TRIMESTRES)

def bulletin_classe(classe_id, periode):
    db = get_db()
    cl = db.execute("SELECT * FROM classes WHERE id=?", (classe_id,)).fetchone()
    eleves = db.execute("SELECT * FROM eleves WHERE classe_id=? ORDER BY nom", (classe_id,)).fetchall()
    # matières concernées (celles notées sur la période OU liées à la classe)
    mats = db.execute("""SELECT DISTINCT m.id, m.nom, COALESCE(cm.coefficient,1) coef FROM notes n JOIN matieres m ON m.id=n.matiere_id
                         LEFT JOIN classe_matiere cm ON cm.classe_id=n.classe_id AND cm.matiere_id=m.id
                         WHERE n.classe_id=? AND n.periode=?""", (classe_id, periode)).fetchall()
    if not mats:
        mats = db.execute("""SELECT m.id, m.nom, COALESCE(cm.coefficient,1) coef FROM matieres m LEFT JOIN classe_matiere cm ON cm.matiere_id=m.id AND cm.classe_id=?
                             WHERE m.cycle=? ORDER BY m.nom LIMIT 12""", (classe_id, cl["cycle"] if cl else "Primaire")).fetchall()
    resultats = []
    for e in eleves:
        notes = db.execute("SELECT matiere_id, note, coef FROM notes WHERE eleve_id=? AND classe_id=? AND periode=?", (e["id"], classe_id, periode)).fetchall()
        d = {n["matiere_id"]: n for n in notes}
        tot = 0; totcoef = 0; det = []
        for m in mats:
            n = d.get(m["id"])
            cf = n["coef"] if n else m["coef"]
            if n:
                tot += n["note"]*cf; totcoef += cf
                det.append({"mat": m["nom"], "note": n["note"], "coef": cf})
            else:
                det.append({"mat": m["nom"], "note": None, "coef": cf})
        moy = round(tot/totcoef, 2) if totcoef else None
        resultats.append({"eleve": e, "moy": moy, "details": det, "totcoef": totcoef})
    resultats.sort(key=lambda r: (r["moy"] is None, -(r["moy"] or 0)))
    rang = 0; prev = None
    for i, r in enumerate(resultats, 1):
        if r["moy"] != prev: rang = i; prev = r["moy"]
        r["rang"] = rang if r["moy"] is not None else "-"
        m = r["moy"]
        r["mention"] = "—" if m is None else ("Excellent" if m>=16 else "Très Bien" if m>=14 else "Bien" if m>=12 else "Assez Bien" if m>=10 else "Passable" if m>=8 else "Insuffisant")
    return cl, mats, resultats

@app.route("/bulletins")
@login_required
def bulletins():
    db = get_db()
    classes = classes_filtrees()
    classe_id = request.args.get("classe_id","")
    periode = request.args.get("periode","")
    cl = mats = resultats = None
    if classe_id and periode:
        cl, mats, resultats = bulletin_classe(classe_id, periode)
    return render_template("bulletins.html", classes=classes, classe_id=classe_id, periode=periode, cl=cl, mats=mats, resultats=resultats, mois=MOIS_PRIMAIRE, trimestres=TRIMESTRES)

@app.route("/bulletins/eleve/<int:eid>")
@login_required
def bulletin_eleve(eid):
    db = get_db()
    periode = request.args.get("periode","")
    e = db.execute("SELECT e.*, cl.nom classe_nom, cl.cycle FROM eleves e LEFT JOIN classes cl ON cl.id=e.classe_id WHERE e.id=?", (eid,)).fetchone()
    notes = db.execute("""SELECT m.nom mat, n.note, n.coef, n.periode FROM notes n JOIN matieres m ON m.id=n.matiere_id
                          WHERE n.eleve_id=? AND n.periode=? ORDER BY m.nom""", (eid, periode)).fetchall()
    moy = round(sum(n["note"]*n["coef"] for n in notes)/sum(n["coef"] for n in notes),2) if notes else None
    return render_template("bulletin_print.html", e=e, notes=notes, moy=moy, periode=periode)

# ---------- Personnel ----------
@app.route("/personnel", methods=["GET","POST"])
@role_required("Directeur","Secrétaire")
def personnel():
    db = get_db()
    if request.method == "POST":
        f = request.form
        mat = f"STF-{datetime.date.today().year}-{(db.execute('SELECT COUNT(*) c FROM personnel').fetchone()['c']+1):03d}"
        db.execute("""INSERT INTO personnel(matricule,nom,prenoms,fonction,telephone,email,matiere,salaire_base,date_embauche,statut)
                      VALUES(?,?,?,?,?,?,?,?,?,?)""",
                   (mat, f.get("nom","").upper().strip(), f.get("prenoms","").strip(), f.get("fonction","Enseignant"),
                    f.get("telephone",""), f.get("email",""), f.get("matiere",""), int(f.get("salaire_base") or 0),
                    f.get("date_embauche") or datetime.date.today().isoformat(), "Actif"))
        db.commit(); flash(f"Personnel enregistré : {mat}", "success")
        return redirect(url_for("personnel"))
    rows = db.execute("SELECT * FROM personnel ORDER BY fonction, nom").fetchall()
    return render_template("personnel.html", rows=rows)

@app.route("/personnel/<int:pid>/supprimer", methods=["POST"])
@login_required
def personnel_supprimer(pid):
    db = get_db(); db.execute("DELETE FROM personnel WHERE id=?", (pid,)); db.commit()
    flash("Personnel supprimé.", "warning")
    return redirect(url_for("personnel"))

# ---------- Présences & Discipline ----------
@app.route("/presences", methods=["GET","POST"])
@role_required("Directeur","Secrétaire","Enseignant","Surveillant")
def presences():
    db = get_db()
    classes = classes_filtrees()
    date_j = request.values.get("date") or datetime.date.today().isoformat()
    classe_id = request.values.get("classe_id","")
    cours_nom = request.values.get("cours","")
    eleves = db.execute("SELECT * FROM eleves WHERE classe_id=? ORDER BY nom", (classe_id,)).fetchall() if classe_id else []
    exist = {}
    if classe_id:
        q = "SELECT * FROM presences WHERE classe_id=? AND date=?"
        params = [classe_id, date_j]
        if cours_nom:
            q += " AND cours=?"; params.append(cours_nom)
        for r in db.execute(q, params).fetchall():
            exist[r["eleve_id"]] = r
    if request.method == "POST" and "appel" in request.form:
        cours_nom = request.form.get("cours","")
        db.execute("DELETE FROM presences WHERE classe_id=? AND date=? AND cours=?", (classe_id, date_j, cours_nom))
        for e in eleves:
            st = request.form.get(f"st_{e['id']}", "Présent")
            mo = request.form.get(f"mo_{e['id']}", "")
            db.execute("INSERT INTO presences(eleve_id,classe_id,date,statut,motif,cours) VALUES(?,?,?,?,?,?)", (e["id"], classe_id, date_j, st, mo, cours_nom))
        db.commit(); flash(f"Appel du {date_j} ({cours_nom or 'journée'}) enregistré.", "success")
        return redirect(url_for("presences", classe_id=classe_id, date=date_j, cours=cours_nom))
    # synthèse 7 derniers jours
    synth = db.execute("""SELECT date, SUM(CASE WHEN statut='Absent' THEN 1 ELSE 0 END) abs, SUM(CASE WHEN statut='Retard' THEN 1 ELSE 0 END) ret, COUNT(*) tot
                          FROM presences GROUP BY date ORDER BY date DESC LIMIT 10""").fetchall()
    # preparation notifications pour absents du jour/classe affiches
    notifs = {}
    for e in eleves:
        ex = exist.get(e["id"])
        if ex and ex["statut"] == "Absent":
            msg = f"Bonjour, votre enfant {e['nom']} {e['prenoms']} est note ABSENT le {date_j} ({cours_nom or 'appel du jour'}). Merci de contacter l'ecole."
            notifs[e["id"]] = {"wa": wa_link(e["telephone_tuteur"] or "", msg), "sms": sms_link(e["telephone_tuteur"] or "", msg), "msg": msg}
    return render_template("presences.html", classes=classes, eleves=eleves, classe_id=classe_id, date_j=date_j, exist=exist, synth=synth, cours_nom=cours_nom, notifs=notifs)

@app.route("/discipline", methods=["GET","POST"])
@login_required
def discipline():
    db = get_db(); an = annee_active()
    if request.method == "POST":
        f = request.form
        db.execute("INSERT INTO disciplines(eleve_id,date,type,motif,sanction,auteur) VALUES(?,?,?,?,?,?)",
                   (f.get("eleve_id"), f.get("date") or datetime.date.today().isoformat(), f.get("type"), f.get("motif"), f.get("sanction",""), session.get("nom","")))
        db.commit(); flash("Fiche discipline enregistrée.", "success")
        return redirect(url_for("discipline"))
    rows = db.execute("""SELECT d.*, e.nom, e.prenoms, e.matricule, cl.nom classe_nom FROM disciplines d JOIN eleves e ON e.id=d.eleve_id
                         LEFT JOIN classes cl ON cl.id=e.classe_id ORDER BY d.date DESC LIMIT 100""").fetchall()
    eleves = db.execute("SELECT id, nom, prenoms, matricule FROM eleves WHERE annee_id=? ORDER BY nom LIMIT 500", (an["id"],)).fetchall()
    return render_template("discipline.html", rows=rows, eleves=eleves)

# ---------- Finances ----------
@app.route("/finances")
@role_required("Directeur","Secrétaire","Comptable")
def finances():
    db = get_db(); an = annee_active()
    total_rec = db.execute("SELECT COALESCE(SUM(montant),0) s FROM paiements WHERE annee_id=?", (an["id"],)).fetchone()["s"]
    total_dep = db.execute("SELECT COALESCE(SUM(montant),0) s FROM depenses").fetchone()["s"]
    par_rub = db.execute("SELECT rubrique, SUM(montant) s, COUNT(*) n FROM paiements WHERE annee_id=? GROUP BY rubrique", (an["id"],)).fetchall()
    recents = db.execute("""SELECT p.*, e.nom, e.prenoms, e.matricule FROM paiements p JOIN eleves e ON e.id=p.eleve_id
                            WHERE p.annee_id=? ORDER BY p.id DESC LIMIT 15""", (an["id"],)).fetchall()
    deps = db.execute("SELECT * FROM depenses ORDER BY date DESC LIMIT 15").fetchall()
    # impayés top
    eleves = db.execute("""SELECT e.id, e.nom, e.prenoms, e.matricule, cl.nom classe_nom, cl.frais_inscription, cl.frais_scolarite
                           FROM eleves e LEFT JOIN classes cl ON cl.id=e.classe_id WHERE e.annee_id=?""", (an["id"],)).fetchall()
    impayes = []
    for e in eleves:
        du = (e["frais_inscription"] or 0) + (e["frais_scolarite"] or 0)
        paye = db.execute("SELECT COALESCE(SUM(montant),0) s FROM paiements WHERE eleve_id=?", (e["id"],)).fetchone()["s"]
        if paye < du: impayes.append({"e": e, "du": du, "paye": paye, "reste": du-paye})
    impayes.sort(key=lambda x: -x["reste"])
    return render_template("finances.html", total_rec=total_rec, total_dep=total_dep, solde=total_rec-total_dep, par_rub=par_rub, recents=recents, deps=deps, impayes=impayes[:20], annee=an)

@app.route("/finances/paiement", methods=["GET","POST"])
@role_required("Directeur","Secrétaire","Comptable")
def paiement():
    db = get_db(); an = annee_active()
    if request.method == "POST":
        f = request.form
        try:
            montant = int(f.get("montant") or 0)
            assert montant > 0
        except: flash("Montant invalide.", "danger"); return redirect(url_for("paiement"))
        recu = nouveau_recu()
        db.execute("INSERT INTO paiements(eleve_id,date,rubrique,montant,mode,recu_no,auteur,annee_id) VALUES(?,?,?,?,?,?,?,?)",
                   (f.get("eleve_id"), f.get("date") or datetime.date.today().isoformat(), f.get("rubrique"), montant, f.get("mode"), recu, session.get("nom",""), an["id"]))
        db.commit(); flash(f"Paiement {recu} : {montant:,} FCFA enregistré.", "success")
        return redirect(url_for("finances"))
    eleves = db.execute("SELECT id, nom, prenoms, matricule FROM eleves WHERE annee_id=? ORDER BY nom", (an["id"],)).fetchall()
    preselect = request.args.get("eleve_id","")
    return render_template("paiement.html", eleves=eleves, rubriques=RUBRIQUES, modes=MODES_PAIEMENT, preselect=preselect, today=datetime.date.today().isoformat())

@app.route("/finances/depense", methods=["POST"])
@role_required("Directeur","Secrétaire","Comptable")
def depense():
    db = get_db()
    f = request.form
    db.execute("INSERT INTO depenses(date,categorie,libelle,montant,beneficiaire,auteur) VALUES(?,?,?,?,?,?)",
               (f.get("date") or datetime.date.today().isoformat(), f.get("categorie","Fonctionnement"), f.get("libelle",""), int(f.get("montant") or 0), f.get("beneficiaire",""), session.get("nom","")))
    db.commit(); flash("Dépense enregistrée.", "success")
    return redirect(url_for("finances"))

@app.route("/recu/<recu_no>")
@login_required
def recurecu(recu_no):
    db = get_db()
    p = db.execute("SELECT p.*, e.nom, e.prenoms, e.matricule, cl.nom classe_nom FROM paiements p JOIN eleves e ON e.id=p.eleve_id LEFT JOIN classes cl ON cl.id=e.classe_id WHERE p.recu_no=?", (recu_no,)).fetchone()
    return render_template("recu.html", p=p)

# ---------- Administration ----------
@app.route("/admin", methods=["GET","POST"])
@login_required
def admin():
    db = get_db()
    if session.get("role") not in GESTION_ACCES:
        flash("Gestion des accès réservée : Directeur, Secrétaire.", "danger"); return redirect(url_for("dashboard"))
    if request.method == "POST":
        action = request.form.get("action")
        if action == "annee":
            if session.get("role") != "Directeur":
                flash("Année scolaire : Directeur uniquement.", "danger"); return redirect(url_for("admin"))
            lib = request.form.get("libelle","").strip()
            if lib:
                db.execute("UPDATE annees SET active=0")
                db.execute("INSERT OR IGNORE INTO annees(libelle, active) VALUES(?,0)", (lib,))
                db.execute("UPDATE annees SET active=1 WHERE libelle=?", (lib,))
                db.commit(); flash(f"Année active : {lib}", "success")
        elif action == "user":
            new_role = request.form.get("role")
            if new_role == "Directeur" and session.get("role") != "Directeur":
                flash("Seul le Directeur peut créer un accès Directeur.", "danger"); return redirect(url_for("admin"))
            try:
                db.execute("INSERT INTO users(nom,username,password,role) VALUES(?,?,?,?)",
                           (request.form.get("nom"), request.form.get("username"), generate_password_hash(request.form.get("password") or "1234"), new_role))
                db.commit(); flash(f"Accès créé : {request.form.get('username')} ({new_role}).", "success")
            except sqlite3.IntegrityError:
                flash("Nom d'utilisateur déjà utilisé.", "danger")
        elif action == "user_edit":
            uid = request.form.get("uid"); new_role = request.form.get("role"); new_pwd = request.form.get("password","")
            cible = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if not cible: flash("Utilisateur introuvable.", "danger")
            elif cible["role"] == "Directeur" and session.get("role") != "Directeur":
                flash("Seul le Directeur peut modifier un Directeur.", "danger")
            elif new_role == "Directeur" and session.get("role") != "Directeur":
                flash("Seul le Directeur peut donner le rôle Directeur.", "danger")
            else:
                if new_pwd: db.execute("UPDATE users SET nom=?, username=?, password=?, role=? WHERE id=?", (request.form.get("nom"), request.form.get("username"), generate_password_hash(new_pwd), new_role, uid))
                else: db.execute("UPDATE users SET nom=?, username=?, role=? WHERE id=?", (request.form.get("nom"), request.form.get("username"), new_role, uid))
                db.commit(); flash("Accès modifié.", "success")
        elif action == "user_del":
            uid = request.form.get("uid")
            cible = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if cible and cible["username"] == "admin": flash("Compte admin protégé.", "danger")
            elif cible and cible["role"] == "Directeur" and session.get("role") != "Directeur": flash("Seul le Directeur peut supprimer un Directeur.", "danger")
            elif cible and int(uid) == int(session.get("user_id")): flash("Vous ne pouvez pas supprimer votre propre compte.", "danger")
            else: db.execute("DELETE FROM users WHERE id=?", (uid,)); db.commit(); flash("Accès supprimé/annulé.", "warning")
        elif action == "ecole":
            nom = request.form.get("ecole_nom","").strip() or "Mon École Privée"
            typ = request.form.get("type_etab", "Mixte")
            db.execute("INSERT OR REPLACE INTO settings(cle,valeur) VALUES('ecole_nom',?)", (nom,))
            db.execute("INSERT OR REPLACE INTO settings(cle,valeur) VALUES('type_etab',?)", (typ,))
            db.commit(); flash(f"École configurée : {nom} ({typ}). Listes filtrées automatiquement.", "success")
        return redirect(url_for("admin"))
    annees = db.execute("SELECT * FROM annees ORDER BY libelle DESC").fetchall()
    users = db.execute("SELECT id, nom, username, role FROM users ORDER BY role, nom").fetchall()
    counts = {t: db.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"] for t in ["eleves","classes","matieres","notes","personnel","presences","disciplines","paiements","depenses"]}
    settings = {r["cle"]: r["valeur"] for r in db.execute("SELECT * FROM settings").fetchall()}
    return render_template("admin.html", annees=annees, users=users, counts=counts, annee=annee_active(), settings=settings)

@app.route("/admin/reset-notes", methods=["POST"])
@login_required
def reset_notes():
    return redirect(url_for("admin"))

@app.route("/export/eleves.csv")
@login_required
def export_eleves():
    db = get_db(); an = annee_active()
    rows = db.execute("""SELECT e.matricule, e.nom, e.prenoms, e.sexe, e.date_naissance, e.cycle, cl.nom classe, e.tuteur, e.telephone_tuteur
                         FROM eleves e LEFT JOIN classes cl ON cl.id=e.classe_id WHERE e.annee_id=? ORDER BY cl.nom, e.nom""", (an["id"],)).fetchall()
    out = io.StringIO(); w = csv.writer(out)
    w.writerow(["Matricule","Nom","Prénoms","Sexe","Naissance","Cycle","Classe","Tuteur","Téléphone"])
    for r in rows: w.writerow([r["matricule"],r["nom"],r["prenoms"],r["sexe"],r["date_naissance"],r["cycle"],r["classe"],r["tuteur"],r["telephone_tuteur"]])
    data = io.BytesIO(out.getvalue().encode("utf-8-sig"))
    return send_file(data, mimetype="text/csv", as_attachment=True, download_name="eleves.csv")

def format_wa(numero):
    """Normalise un numero vers format international WhatsApp (defaut CI 225)."""
    if not numero: return ""
    d = re.sub(r"\D", "", numero)
    if len(d) == 10 and d.startswith("0"): d = "225" + d[1:]
    elif len(d) == 10 and d.startswith(("0","7","0","5")): d = "225" + d
    elif len(d) == 8: d = "225" + d  # numero local 8 chiffres CI
    return d

def wa_link(numero, message):
    return f"https://wa.me/{format_wa(numero)}?text={quote(message)}" if format_wa(numero) else ""

def sms_link(numero, message):
    d = re.sub(r"[^+\d]", "", numero or "")
    return f"sms:{d}?body={quote(message)}" if d else ""

def parent_required(f):
    @wraps(f)
    def d(*a, **kw):
        if "parent_eleve_id" not in session:
            return redirect(url_for("parent_login"))
        return f(*a, **kw)
    return d

def get_setting(cle, defaut=""):
    try:
        r = get_db().execute("SELECT valeur FROM settings WHERE cle=?", (cle,)).fetchone()
        return r["valeur"] if r else defaut
    except: return defaut

def classes_filtrees():
    """Retourne les classes selon le type d'établissement configuré."""
    t = get_setting("type_etab", "Mixte")
    db = get_db()
    if t == "Primaire seul":
        return db.execute("SELECT * FROM classes WHERE cycle='Primaire' ORDER BY nom").fetchall()
    if t == "Secondaire seul":
        return db.execute("SELECT * FROM classes WHERE cycle='Secondaire' ORDER BY nom").fetchall()
    return db.execute("SELECT * FROM classes ORDER BY cycle, nom").fetchall()

@app.context_processor
def inject_ecole():
    try: return dict(ecole_nom=get_setting("ecole_nom", "Mon École Privée"), type_etab=get_setting("type_etab", "Mixte"))
    except: return dict(ecole_nom="Mon École Privée", type_etab="Mixte")

@app.route("/sauvegarde")
@login_required
def sauvegarde():
    return send_file(DB_PATH, as_attachment=True, download_name=f"sauvegarde-ecole-{datetime.date.today().isoformat()}.db")

# ================= V2 : ESPACE PARENT =================
@app.route("/parent/login", methods=["GET","POST"])
def parent_login():
    if request.method == "POST":
        nom = request.form.get("username","").strip().upper()
        pwd = request.form.get("password","").strip()
        db = get_db()
        e = db.execute("SELECT e.*, cl.nom classe_nom FROM eleves e LEFT JOIN classes cl ON cl.id=e.classe_id WHERE UPPER(e.nom)=?", (nom,)).fetchone()
        # Si plusieurs homonymes, on cherche aussi prenom/matricule : on prend le 1er, puis verifie password
        if not e:
            # essai : username = "NOM PRENOMS" ?
            e = db.execute("SELECT e.*, cl.nom classe_nom FROM eleves e LEFT JOIN classes cl ON cl.id=e.classe_id WHERE UPPER(e.nom || ' ' || e.prenoms)=?", (nom,)).fetchone()
        ok = False; pid = None
        if e:
            p = db.execute("SELECT * FROM parents WHERE eleve_id=?", (e["id"],)).fetchone()
            if p and p["password"]:
                ok = check_password_hash(p["password"], pwd)
            else:
                ok = (pwd == e["matricule"])  # mot de passe initial = matricule
                if ok:
                    db.execute("INSERT OR IGNORE INTO parents(eleve_id, telephone, must_change) VALUES(?,?,1)", (e["id"], e["telephone_tuteur"] or ""))
                    db.commit()
        if e and ok:
            session["parent_eleve_id"] = e["id"]; session["parent_nom"] = f"{e['nom']} {e['prenoms']}"
            p2 = db.execute("SELECT * FROM parents WHERE eleve_id=?", (e["id"],)).fetchone()
            if p2 and p2["must_change"]:
                flash("Bienvenue ! Changez votre mot de passe initial (matricule) pour sécuriser l'accès.", "warning")
                return redirect(url_for("parent_password"))
            return redirect(url_for("parent_dash"))
        flash("Nom d'élève ou matricule incorrect. Demandez le matricule au secrétariat.", "danger")
    return render_template("parent_login.html")

@app.route("/parent/logout")
def parent_logout():
    session.pop("parent_eleve_id", None); session.pop("parent_nom", None)
    return redirect(url_for("parent_login"))

@app.route("/parent/password", methods=["GET","POST"])
@parent_required
def parent_password():
    db = get_db()
    if request.method == "POST":
        n1 = request.form.get("n1",""); n2 = request.form.get("n2","")
        if len(n1) < 4: flash("Mot de passe trop court (4 caractères min).", "danger")
        elif n1 != n2: flash("Les deux mots de passe ne correspondent pas.", "danger")
        else:
            db.execute("UPDATE parents SET password=?, must_change=0 WHERE eleve_id=?", (generate_password_hash(n1), session["parent_eleve_id"]))
            db.commit(); flash("Mot de passe changé avec succès.", "success")
            return redirect(url_for("parent_dash"))
    return render_template("parent_password.html")

@app.route("/parent")
@parent_required
def parent_dash():
    db = get_db(); eid = session["parent_eleve_id"]
    e = db.execute("SELECT e.*, cl.nom classe_nom, cl.cycle, cl.titulaire, cl.frais_inscription, cl.frais_scolarite FROM eleves e LEFT JOIN classes cl ON cl.id=e.classe_id WHERE e.id=?", (eid,)).fetchone()
    notes = db.execute("""SELECT m.nom mat, n.note, n.coef, n.periode, n.periode_type FROM notes n JOIN matieres m ON m.id=n.matiere_id
                          WHERE n.eleve_id=? ORDER BY n.periode, m.nom""", (eid,)).fetchall()
    # moyennes par matiere
    par_mat = {}
    for n in notes:
        par_mat.setdefault(n["mat"], []).append((n["note"], n["coef"]))
    moy_mat = {m: round(sum(v*c for v,c in l)/sum(c for _,c in l),2) for m,l in par_mat.items()}
    moy_gen = round(sum(n["note"]*n["coef"] for n in notes)/sum(n["coef"] for n in notes),2) if notes else None
    blancs = [n for n in notes if "blanc" in (n["periode"] or "").lower()]
    absences = db.execute("SELECT * FROM presences WHERE eleve_id=? ORDER BY date DESC LIMIT 30", (eid,)).fetchall()
    nb_abs = db.execute("SELECT COUNT(*) c FROM presences WHERE eleve_id=? AND statut='Absent'", (eid,)).fetchone()["c"]
    nb_ret = db.execute("SELECT COUNT(*) c FROM presences WHERE eleve_id=? AND statut='Retard'", (eid,)).fetchone()["c"]
    pays = db.execute("SELECT * FROM paiements WHERE eleve_id=? ORDER BY date DESC", (eid,)).fetchall()
    total_du = (e["frais_inscription"] or 0) + (e["frais_scolarite"] or 0)
    total_paye = sum(p["montant"] for p in pays)
    disc = db.execute("SELECT * FROM disciplines WHERE eleve_id=? ORDER BY date DESC LIMIT 10", (eid,)).fetchone()
    res = db.execute("SELECT r.*, cl.nom classe_nom FROM ressources r LEFT JOIN classes cl ON cl.id=r.classe_id WHERE r.classe_id=? OR r.classe_id IS NULL ORDER BY r.date_pub DESC LIMIT 20", (e["classe_id"],)).fetchall()
    devs = db.execute("SELECT * FROM devoirs WHERE classe_id=? ORDER BY date_limite LIMIT 20", (e["classe_id"],)).fetchall()
    crs = db.execute("SELECT * FROM cours WHERE classe_id=? ORDER BY date_pub DESC LIMIT 20", (e["classe_id"],)).fetchall()
    fichiers = db.execute("SELECT * FROM fichiers WHERE classe_id=? OR classe_id IS NULL ORDER BY date_pub DESC LIMIT 30", (e["classe_id"],)).fetchall()
    return render_template("parent_dash.html", e=e, notes=notes, moy_mat=moy_mat, moy_gen=moy_gen, blancs=blancs,
                           absences=absences, nb_abs=nb_abs, nb_ret=nb_ret, paiements=pays, total_du=total_du,
                           total_paye=total_paye, reste=total_du-total_paye, ressources=res, devoirs=devs, cours=crs, fichiers=fichiers)

# ---- Demande d'inscription publique (parent, sans compte) ----
@app.route("/demande-inscription", methods=["GET","POST"])
def demande_inscription():
    db = get_db()
    classes = classes_filtrees()
    if request.method == "POST":
        f = request.form
        an = annee_active()
        cur = db.execute("""INSERT INTO demandes(nom,prenoms,sexe,date_naissance,classe_souhaitee,tuteur,telephone_tuteur,adresse,statut,date_demande,annee_id)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                         (f.get("nom","").upper().strip(), f.get("prenoms","").strip(), f.get("sexe","M"), f.get("date_naissance",""),
                          f.get("classe_souhaitee",""), f.get("tuteur",""), f.get("telephone_tuteur",""), f.get("adresse",""),
                          "En attente", datetime.date.today().isoformat(), an["id"] if an else None))
        db.commit()
        return redirect(url_for("demande_print", did=cur.lastrowid))
    return render_template("demande_form.html", classes=classes)

@app.route("/demande/<int:did>/print")
def demande_print(did):
    db = get_db()
    d = db.execute("SELECT * FROM demandes WHERE id=?", (did,)).fetchone()
    return render_template("demande_print.html", d=d)

@app.route("/admin/demandes")
@login_required
def demandes_admin():
    db = get_db()
    rows = db.execute("SELECT * FROM demandes ORDER BY date_demande DESC").fetchall()
    return render_template("demandes.html", rows=rows)

@app.route("/admin/demandes/<int:did>/valider", methods=["POST"])
@login_required
def demande_valider(did):
    db = get_db(); an = annee_active()
    d = db.execute("SELECT * FROM demandes WHERE id=?", (did,)).fetchone()
    if not d: return redirect(url_for("demandes_admin"))
    cl = db.execute("SELECT * FROM classes WHERE nom=?", (d["classe_souhaitee"],)).fetchone()
    mat = nouveau_matricule()
    db.execute("""INSERT INTO eleves(matricule,nom,prenoms,sexe,date_naissance,cycle,classe_id,tuteur,telephone_tuteur,adresse,statut,annee_id,date_inscription)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               (mat, d["nom"], d["prenoms"], d["sexe"], d["date_naissance"], cl["cycle"] if cl else "Primaire",
                cl["id"] if cl else None, d["tuteur"], d["telephone_tuteur"], d["adresse"], "Inscrit", an["id"], datetime.date.today().isoformat()))
    db.execute("UPDATE demandes SET statut='Validée' WHERE id=?", (did,))
    db.commit(); flash(f"Demande validée → élève {mat} créé. Informez le parent (login: NOM + {mat}).", "success")
    return redirect(url_for("demandes_admin"))

# ---- Contenus pedagogiques (staff) ----
@app.route("/contenus", methods=["GET","POST"])
@role_required("Directeur","Secrétaire","Enseignant")
def contenus():
    db = get_db()
    classes = classes_filtrees()
    if request.method == "POST":
        f = request.form; kind = f.get("kind")
        today = datetime.date.today().isoformat()
        if kind == "ressource":
            db.execute("INSERT INTO ressources(classe_id,matiere,titre,description,lien,date_pub) VALUES(?,?,?,?,?,?)",
                       (f.get("classe_id") or None, f.get("matiere",""), f.get("titre",""), f.get("description",""), f.get("lien",""), today))
        elif kind == "devoir":
            db.execute("INSERT INTO devoirs(classe_id,matiere,titre,consigne,date_donnee,date_limite) VALUES(?,?,?,?,?,?)",
                       (f.get("classe_id"), f.get("matiere",""), f.get("titre",""), f.get("consigne",""), today, f.get("date_limite","")))
        elif kind == "cours":
            db.execute("INSERT INTO cours(classe_id,matiere,titre,contenu,date_pub) VALUES(?,?,?,?,?)",
                       (f.get("classe_id"), f.get("matiere",""), f.get("titre",""), f.get("contenu",""), today))
        db.commit(); flash("Contenu publié (visible espace parent).", "success")
        return redirect(url_for("contenus"))
    res = db.execute("SELECT r.*, cl.nom classe_nom FROM ressources r LEFT JOIN classes cl ON cl.id=r.classe_id ORDER BY r.id DESC LIMIT 30").fetchall()
    devs = db.execute("SELECT d.*, cl.nom classe_nom FROM devoirs d LEFT JOIN classes cl ON cl.id=d.classe_id ORDER BY d.id DESC LIMIT 30").fetchall()
    crs = db.execute("SELECT c.*, cl.nom classe_nom FROM cours c LEFT JOIN classes cl ON cl.id=c.classe_id ORDER BY c.id DESC LIMIT 30").fetchall()
    fichiers = db.execute("SELECT f.*, cl.nom classe_nom FROM fichiers f LEFT JOIN classes cl ON cl.id=f.classe_id ORDER BY f.id DESC LIMIT 30").fetchall()
    return render_template("contenus.html", classes=classes, ressources=res, devoirs=devs, cours=crs, fichiers=fichiers)

# ---- Fichiers : import Word/Excel/Image/PDF (staff) -> telechargement parent ----
def fichier_ok(nom):
    return "." in nom and nom.rsplit(".",1)[1].lower() in ALLOWED_EXT

@app.route("/fichiers/upload", methods=["POST"])
@role_required("Directeur","Secrétaire","Enseignant")
def fichier_upload():
    db = get_db()
    f = request.files.get("fichier")
    if not f or not f.filename: flash("Choisissez un fichier.", "danger"); return redirect(url_for("contenus"))
    if not fichier_ok(f.filename): flash(f"Type refusé. Acceptés : {', '.join(sorted(ALLOWED_EXT))} (max {MAX_MB} Mo).", "danger"); return redirect(url_for("contenus"))
    safe = secure_filename(f.filename)
    stamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    fname = f"{stamp}_{safe}"
    f.save(os.path.join(UPLOAD_DIR, fname))
    taille = os.path.getsize(os.path.join(UPLOAD_DIR, fname))
    db.execute("INSERT INTO fichiers(classe_id,matiere,titre,type_doc,filename,original,taille,date_pub,auteur) VALUES(?,?,?,?,?,?,?,?,?)",
               (request.form.get("classe_id") or None, request.form.get("matiere",""), request.form.get("titre") or safe,
                request.form.get("type_doc","Cours"), fname, safe, taille, datetime.date.today().isoformat(), session.get("nom","")))
    db.commit(); flash(f"Fichier importé : {safe} (visible parents).", "success")
    return redirect(url_for("contenus"))

@app.route("/fichiers/<int:fid>/download")
def fichier_download(fid):
    # staff connecté OU parent connecté
    if "user_id" not in session and "parent_eleve_id" not in session:
        flash("Connectez-vous pour télécharger.", "danger"); return redirect(url_for("parent_login"))
    db = get_db()
    r = db.execute("SELECT * FROM fichiers WHERE id=?", (fid,)).fetchone()
    if not r: flash("Fichier introuvable.", "danger"); return redirect(url_for("contenus") if "user_id" in session else url_for("parent_dash"))
    return send_from_directory(UPLOAD_DIR, r["filename"], as_attachment=True, download_name=r["original"])

@app.route("/fichiers/<int:fid>/supprimer", methods=["POST"])
@role_required("Directeur","Secrétaire")
def fichier_supprimer(fid):
    db = get_db()
    r = db.execute("SELECT * FROM fichiers WHERE id=?", (fid,)).fetchone()
    if r:
        try: os.remove(os.path.join(UPLOAD_DIR, r["filename"]))
        except: pass
        db.execute("DELETE FROM fichiers WHERE id=?", (fid,)); db.commit(); flash("Fichier supprimé.", "warning")
    return redirect(url_for("contenus"))

# ---- Notification absence : log + liens WhatsApp/SMS ----
@app.route("/notifier/<int:eid>", methods=["POST"])
@login_required
def notifier(eid):
    db = get_db()
    canal = request.form.get("canal", "WhatsApp")
    msg = request.form.get("message", "")
    db.execute("INSERT INTO notif_log(eleve_id,date,canal,message,auteur) VALUES(?,?,?,?,?)",
               (eid, datetime.datetime.now().isoformat(timespec="minutes"), canal, msg, session.get("nom","")))
    db.commit(); flash(f"Notification {canal} enregistrée.", "success")
    return redirect(request.referrer or url_for("presences"))

if __name__ == "__main__":
    init_db()
    print("="*60); print(" GESTION ECOLE PRIVEE - http://localhost:5000  (admin/admin123)"); print("="*60)
    app.run(host="0.0.0.0", port=5000, debug=True)
