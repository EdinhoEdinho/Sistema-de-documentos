import sys
import threading
import time
import webbrowser
from datetime import date, datetime
from functools import wraps
import os
from pathlib import Path
import secrets
import tkinter as tk
from tkinter import messagebox

import mysql.connector
from mysql.connector import Error as MySQLError
from flask import Flask, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash


if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).resolve().parent

app = Flask(
    __name__,
    template_folder=str(BASE_DIR),
    static_folder=str(BASE_DIR / "static"),
)
app.secret_key = "erp-local-secret"
app.config["SESSION_COOKIE_HTTPONLY"] = True
APP_INSTANCE_TOKEN = secrets.token_hex(16)

DB_CONFIG = {
    "host": os.getenv("DB_HOST", ""),
    "user": os.getenv("DB_USER", ""),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", ""),
    "port": int(os.getenv("DB_PORT", "3306")),
}


def get_conn():
    return mysql.connector.connect(**DB_CONFIG)


def fetch_all(query, params=None):
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(query, params or [])
        cols = [c[0] for c in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]
    finally:
        conn.close()


def execute(query, params=None):
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(query, params or [])
        conn.commit()
    finally:
        conn.close()


def parse_alert_days(field_name, default=30):
    value = request.form.get(field_name, default)
    return parse_alert_days_value(value)


def parse_alert_days_value(value):
    try:
        days = int(value)
        if days >= 0:
            return days
        return None
    except ValueError:
        return None


def parse_date_field(field_name, label):
    value = request.form.get(field_name) or None
    if not value:
        return None, None
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return value, None
    except ValueError:
        return None, f"Informe uma data valida para {label}."


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id") or session.get("app_instance_token") != APP_INSTANCE_TOKEN:
            session.clear()
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def get_user_by_username(username):
    rows = fetch_all(
        "SELECT id, nome, usuario, senha_hash, ativo FROM Usuario WHERE usuario = %s LIMIT 1",
        [username],
    )
    return rows[0] if rows else None


@app.before_request
def require_login():
    allowed = {"static", "login", "logout"}
    if request.endpoint in allowed or request.endpoint is None:
        return None
    if not session.get("user_id") or session.get("app_instance_token") != APP_INSTANCE_TOKEN:
        session.clear()
        return redirect(url_for("login"))
    return None


def fetch_alertas():
    return fetch_all(
        """
        SELECT c.Colid, c.Colnome, e.Empnome, 'NR' AS Tipo, n.Nrsnumero AS Codigo,
               n.Nrsdata AS Vencimento, COALESCE(n.Nrsalertadias, 30) AS AvisarDias,
               DATEDIFF(n.Nrsdata, CURDATE()) AS DiasRestantes,
               CASE WHEN n.Nrsdata < CURDATE() THEN 'VENCIDO'
                    WHEN n.Nrsdata = CURDATE() THEN 'VENCE_HOJE'
                    ELSE 'PROXIMO' END AS StatusAlerta
        FROM Nrs n
        INNER JOIN Colaborador c ON c.Colid = n.Colaborador_Colid
        INNER JOIN Empresa e ON e.Empid = c.Empresa_Empid
        WHERE n.Nrsdata IS NOT NULL
          AND (n.Nrsdata < CURDATE()
               OR n.Nrsdata <= DATE_ADD(CURDATE(), INTERVAL COALESCE(n.Nrsalertadias, 30) DAY))
        UNION ALL
        SELECT c.Colid, c.Colnome, e.Empnome, 'ASO' AS Tipo, CAST(a.Asoid AS CHAR(45)) AS Codigo,
               a.Asodata AS Vencimento, COALESCE(a.Asoalertadias, 30) AS AvisarDias,
               DATEDIFF(a.Asodata, CURDATE()) AS DiasRestantes,
               CASE WHEN a.Asodata < CURDATE() THEN 'VENCIDO'
                    WHEN a.Asodata = CURDATE() THEN 'VENCE_HOJE'
                    ELSE 'PROXIMO' END AS StatusAlerta
        FROM Aso a
        INNER JOIN Colaborador c ON c.Colid = a.Colaborador_Colid
        INNER JOIN Empresa e ON e.Empid = c.Empresa_Empid
        WHERE a.Asodata IS NOT NULL
          AND (a.Asodata < CURDATE()
               OR a.Asodata <= DATE_ADD(CURDATE(), INTERVAL COALESCE(a.Asoalertadias, 30) DAY))
        UNION ALL
        SELECT NULL AS Colid, '-' AS Colnome, e.Empnome, 'DOCUMENTO' AS Tipo, d.Docnome AS Codigo,
               d.Docdt AS Vencimento, COALESCE(d.Docalertadias, 30) AS AvisarDias,
               DATEDIFF(d.Docdt, CURDATE()) AS DiasRestantes,
               CASE WHEN d.Docdt < CURDATE() THEN 'VENCIDO'
                    WHEN d.Docdt = CURDATE() THEN 'VENCE_HOJE'
                    ELSE 'PROXIMO' END AS StatusAlerta
        FROM Documentos d
        INNER JOIN Empresa e ON e.Empid = d.Empresa_Empid
        WHERE d.Docdt IS NOT NULL
          AND (d.Docdt < CURDATE()
               OR d.Docdt <= DATE_ADD(CURDATE(), INTERVAL COALESCE(d.Docalertadias, 30) DAY))
        ORDER BY Vencimento ASC
        """
    )


def format_alert_text(alertas):
    if not alertas:
        return "Nao ha alertas pendentes no momento."
    linhas = ["Alertas de vencimento:"]
    for item in alertas:
        if item["StatusAlerta"] == "VENCIDO":
            status = f"Vencido ha {abs(item['DiasRestantes'])} dia(s)"
        elif item["StatusAlerta"] == "VENCE_HOJE":
            status = "Vence hoje"
        else:
            status = f"Vence em {item['DiasRestantes']} dia(s)"
        nome = item["Empnome"]
        if item["Colnome"] != "-":
            nome += " / " + item["Colnome"]
        linhas.append(f"- {item['Tipo']} | {item['Codigo']} | {nome} | {item['Vencimento']} | {status}")
    return "\n".join(linhas)


def alert_popup_once():
    try:
        alertas = fetch_alertas()
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("Alertas de vencimento", format_alert_text(alertas))
        root.destroy()
    except Exception:
        return None


@app.route("/")
@login_required
def dashboard():
    empresas_count = fetch_all("SELECT COUNT(*) AS total FROM Empresa")[0]["total"]
    colaboradores_count = fetch_all("SELECT COUNT(*) AS total FROM Colaborador")[0]["total"]
    documentos_count = fetch_all("SELECT COUNT(*) AS total FROM Documentos")[0]["total"]
    return render_template(
        "dashboard.html",
        empresas_count=empresas_count,
        colaboradores_count=colaboradores_count,
        documentos_count=documentos_count,
        hoje=date.today(),
        alertas=fetch_alertas(),
    )


@app.route("/empresas", methods=["GET", "POST"])
@login_required
def empresas():
    if request.method == "POST":
        nome = request.form.get("empnome", "").strip()
        if not nome:
            flash("Informe o nome da empresa.")
            return redirect(url_for("empresas"))
        execute("INSERT INTO Empresa (Empnome) VALUES (%s)", [nome])
        flash("Empresa cadastrada com sucesso.")
        return redirect(url_for("empresas"))
    data = fetch_all("SELECT Empid, Empnome FROM Empresa ORDER BY Empnome")
    return render_template("empresas.html", empresas=data)


@app.route("/empresas/<int:empid>/editar", methods=["GET", "POST"])
@login_required
def empresas_editar(empid):
    empresa = fetch_all("SELECT Empid, Empnome FROM Empresa WHERE Empid = %s", [empid])
    if not empresa:
        flash("Empresa nao encontrada.")
        return redirect(url_for("empresas"))
    if request.method == "POST":
        nome = request.form.get("empnome", "").strip()
        if not nome:
            flash("Informe o nome da empresa.")
            return redirect(url_for("empresas_editar", empid=empid))
        execute("UPDATE Empresa SET Empnome = %s WHERE Empid = %s", [nome, empid])
        flash("Empresa atualizada com sucesso.")
        return redirect(url_for("empresas"))
    return render_template("empresas_editar.html", empresa=empresa[0])


@app.route("/empresas/<int:empid>/excluir", methods=["POST"])
@login_required
def empresas_excluir(empid):
    try:
        execute("DELETE FROM Empresa WHERE Empid = %s", [empid])
        flash("Empresa excluida com sucesso.")
    except MySQLError:
        flash("Nao foi possivel excluir: existem registros vinculados.")
    return redirect(url_for("empresas"))


def colaborador_form_values():
    dtnasc, dtnasc_error = parse_date_field("coldtnasc", "data de nascimento")
    cadastro, cadastro_error = parse_date_field("colcadastro", "data de cadastro")
    return {
        "nome": request.form.get("colnome", "").strip(),
        "dtnasc": dtnasc,
        "dtnasc_error": dtnasc_error,
        "cadastro": cadastro,
        "cadastro_error": cadastro_error,
        "empresa_id": request.form.get("empresa_empid", "").strip(),
        "endrua": request.form.get("endrua", "").strip() or None,
        "endnumero": request.form.get("endnumero", "").strip() or None,
        "endbairro": request.form.get("endbairro", "").strip() or None,
        "endcidade": request.form.get("endcidade", "").strip() or None,
        "endestado": request.form.get("endestado", "").strip() or None,
        "endpais": request.form.get("endpais", "").strip() or None,
        "endcep": request.form.get("endcep", "").strip() or None,
    }


def endereco_invalido(v):
    preenchido = any(v[k] for k in ["endrua", "endnumero", "endbairro", "endcidade", "endestado", "endpais", "endcep"])
    invalido = preenchido and not (v["endrua"] and v["endcidade"] and v["endestado"])
    return preenchido, invalido


@app.route("/colaboradores", methods=["GET", "POST"])
@login_required
def colaboradores():
    if request.method == "POST":
        v = colaborador_form_values()
        if v["dtnasc_error"]:
            flash(v["dtnasc_error"])
            return redirect(url_for("colaboradores"))
        if v["cadastro_error"]:
            flash(v["cadastro_error"])
            return redirect(url_for("colaboradores"))
        if not v["nome"] or not v["empresa_id"]:
            flash("Nome e empresa sao obrigatorios.")
            return redirect(url_for("colaboradores"))
        endereco_preenchido, invalido = endereco_invalido(v)
        if invalido:
            flash("Para endereco, informe ao menos rua, cidade e estado.")
            return redirect(url_for("colaboradores"))
        conn = get_conn()
        try:
            cursor = conn.cursor()
            endereco_id = None
            if endereco_preenchido:
                cursor.execute(
                    """
                    INSERT INTO Endereco
                    (Endrua, Endnumero, Endbairro, Endcidade, Endestado, Endpais, Endcep)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    [v["endrua"], v["endnumero"], v["endbairro"], v["endcidade"], v["endestado"], v["endpais"], v["endcep"]],
                )
                endereco_id = cursor.lastrowid
            cursor.execute(
                """
                INSERT INTO Colaborador
                (Colnome, Coldtnasc, Colcadastro, Endereco_Endid, Empresa_Empid)
                VALUES (%s, %s, %s, %s, %s)
                """,
                [v["nome"], v["dtnasc"], v["cadastro"], endereco_id, v["empresa_id"]],
            )
            conn.commit()
        except MySQLError as exc:
            flash(f"Nao foi possivel cadastrar o colaborador: {exc}")
            return redirect(url_for("colaboradores"))
        finally:
            conn.close()
        flash("Colaborador cadastrado com sucesso.")
        return redirect(url_for("colaboradores"))
    data = fetch_all(
        """
        SELECT c.Colid, c.Colnome, c.Coldtnasc, c.Colcadastro,
               c.Endereco_Endid, c.Empresa_Empid, e.Empnome,
               ed.Endrua, ed.Endnumero, ed.Endbairro, ed.Endcidade, ed.Endestado, ed.Endpais, ed.Endcep
        FROM Colaborador c
        INNER JOIN Empresa e ON e.Empid = c.Empresa_Empid
        LEFT JOIN Endereco ed ON ed.Endid = c.Endereco_Endid
        ORDER BY c.Colnome
        """
    )
    empresas_data = fetch_all("SELECT Empid, Empnome FROM Empresa ORDER BY Empnome")
    return render_template("colaboradores.html", colaboradores=data, empresas=empresas_data)


@app.route("/colaboradores/<int:colid>/editar", methods=["GET", "POST"])
@login_required
def colaboradores_editar(colid):
    colaborador = fetch_all(
        """
        SELECT c.Colid, c.Colnome, c.Coldtnasc, c.Colcadastro, c.Endereco_Endid, c.Empresa_Empid,
               ed.Endrua, ed.Endnumero, ed.Endbairro, ed.Endcidade, ed.Endestado, ed.Endpais, ed.Endcep
        FROM Colaborador c
        LEFT JOIN Endereco ed ON ed.Endid = c.Endereco_Endid
        WHERE c.Colid = %s
        """,
        [colid],
    )
    if not colaborador:
        flash("Colaborador nao encontrado.")
        return redirect(url_for("colaboradores"))
    colaborador = colaborador[0]
    if request.method == "POST":
        v = colaborador_form_values()
        if v["dtnasc_error"]:
            flash(v["dtnasc_error"])
            return redirect(url_for("colaboradores_editar", colid=colid))
        if v["cadastro_error"]:
            flash(v["cadastro_error"])
            return redirect(url_for("colaboradores_editar", colid=colid))
        if not v["nome"] or not v["empresa_id"]:
            flash("Nome e empresa sao obrigatorios.")
            return redirect(url_for("colaboradores_editar", colid=colid))
        endereco_preenchido, invalido = endereco_invalido(v)
        if invalido:
            flash("Para endereco, informe ao menos rua, cidade e estado.")
            return redirect(url_for("colaboradores_editar", colid=colid))
        conn = get_conn()
        try:
            cursor = conn.cursor()
            endereco_id = colaborador["Endereco_Endid"]
            if endereco_preenchido and endereco_id:
                cursor.execute(
                    """
                        UPDATE Endereco
                        SET Endrua = %s, Endnumero = %s, Endbairro = %s, Endcidade = %s,
                            Endestado = %s, Endpais = %s, Endcep = %s
                        WHERE Endid = %s
                        """,
                    [v["endrua"], v["endnumero"], v["endbairro"], v["endcidade"], v["endestado"], v["endpais"], v["endcep"], endereco_id],
                )
            elif endereco_preenchido:
                cursor.execute(
                    """
                        INSERT INTO Endereco
                        (Endrua, Endnumero, Endbairro, Endcidade, Endestado, Endpais, Endcep)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                    [v["endrua"], v["endnumero"], v["endbairro"], v["endcidade"], v["endestado"], v["endpais"], v["endcep"]],
                )
                endereco_id = cursor.lastrowid
            elif not endereco_preenchido:
                endereco_id = None
            cursor.execute(
                """
                UPDATE Colaborador
                SET Colnome = %s, Coldtnasc = %s, Colcadastro = %s, Endereco_Endid = %s, Empresa_Empid = %s
                WHERE Colid = %s
                """,
                [v["nome"], v["dtnasc"], v["cadastro"], endereco_id, v["empresa_id"], colid],
            )
            conn.commit()
        except MySQLError as exc:
            flash(f"Nao foi possivel atualizar o colaborador: {exc}")
            return redirect(url_for("colaboradores_editar", colid=colid))
        finally:
            conn.close()
        flash("Colaborador atualizado com sucesso.")
        return redirect(url_for("colaboradores"))
    empresas_data = fetch_all("SELECT Empid, Empnome FROM Empresa ORDER BY Empnome")
    return render_template("colaboradores_editar.html", colaborador=colaborador, empresas=empresas_data)


@app.route("/colaboradores/<int:colid>/excluir", methods=["POST"])
@login_required
def colaboradores_excluir(colid):
    try:
        execute("DELETE FROM Colaborador WHERE Colid = %s", [colid])
        flash("Colaborador excluido com sucesso.")
    except MySQLError:
        flash("Nao foi possivel excluir o colaborador.")
    return redirect(url_for("colaboradores"))


@app.route("/nrs", methods=["GET", "POST"])
@login_required
def nrs():
    if request.method == "POST":
        nrsnumero = request.form.get("nrsnumero", "").strip()
        nrsdata, nrsdata_error = parse_date_field("nrsdata", "vencimento da NR")
        nrsalertadias = parse_alert_days("nrsalertadias")
        colaborador_id = request.form.get("colaborador_colid")
        if nrsdata_error:
            flash(nrsdata_error)
            return redirect(url_for("nrs"))
        if not nrsnumero or not colaborador_id:
            flash("Numero da NR e colaborador sao obrigatorios.")
            return redirect(url_for("nrs"))
        if nrsalertadias is None:
            flash("Informe uma quantidade valida de dias para alerta.")
            return redirect(url_for("nrs"))
        execute("INSERT INTO Nrs (Nrsnumero, Nrsdata, Nrsalertadias, Colaborador_Colid) VALUES (%s, %s, %s, %s)", [nrsnumero, nrsdata, nrsalertadias, colaborador_id])
        flash("NR cadastrada com sucesso.")
        return redirect(url_for("nrs"))
    data = fetch_all(
        """
        SELECT n.Nrsid, n.Nrsnumero, n.Nrsdata, n.Nrsalertadias, n.Colaborador_Colid, c.Colnome
        FROM Nrs n INNER JOIN Colaborador c ON c.Colid = n.Colaborador_Colid
        ORDER BY n.Nrsdata ASC, n.Nrsid DESC
        """
    )
    colaboradores_data = fetch_all("SELECT Colid, Colnome FROM Colaborador ORDER BY Colnome")
    return render_template("nrs.html", nrs=data, colaboradores=colaboradores_data)


@app.route("/nrs/<int:nrsid>/editar", methods=["GET", "POST"])
@login_required
def nrs_editar(nrsid):
    nr = fetch_all("SELECT Nrsid, Nrsnumero, Nrsdata, Nrsalertadias, Colaborador_Colid FROM Nrs WHERE Nrsid = %s", [nrsid])
    if not nr:
        flash("NR nao encontrada.")
        return redirect(url_for("nrs"))
    if request.method == "POST":
        nrsnumero = request.form.get("nrsnumero", "").strip()
        nrsdata, nrsdata_error = parse_date_field("nrsdata", "vencimento da NR")
        nrsalertadias = parse_alert_days("nrsalertadias")
        colaborador_id = request.form.get("colaborador_colid")
        if nrsdata_error:
            flash(nrsdata_error)
            return redirect(url_for("nrs_editar", nrsid=nrsid))
        if not nrsnumero or not colaborador_id:
            flash("Numero da NR e colaborador sao obrigatorios.")
            return redirect(url_for("nrs_editar", nrsid=nrsid))
        if nrsalertadias is None:
            flash("Informe uma quantidade valida de dias para alerta.")
            return redirect(url_for("nrs_editar", nrsid=nrsid))
        execute("UPDATE Nrs SET Nrsnumero = %s, Nrsdata = %s, Nrsalertadias = %s, Colaborador_Colid = %s WHERE Nrsid = %s", [nrsnumero, nrsdata, nrsalertadias, colaborador_id, nrsid])
        flash("NR atualizada com sucesso.")
        return redirect(url_for("nrs"))
    colaboradores_data = fetch_all("SELECT Colid, Colnome FROM Colaborador ORDER BY Colnome")
    return render_template("nrs_editar.html", nr=nr[0], colaboradores=colaboradores_data)


@app.route("/nrs/<int:nrsid>/excluir", methods=["POST"])
@login_required
def nrs_excluir(nrsid):
    execute("DELETE FROM Nrs WHERE Nrsid = %s", [nrsid])
    flash("NR excluida com sucesso.")
    return redirect(url_for("nrs"))


@app.route("/aso", methods=["GET", "POST"])
@login_required
def aso():
    if request.method == "POST":
        asodata, asodata_error = parse_date_field("asodata", "vencimento do ASO")
        asoalertadias = parse_alert_days("asoalertadias")
        colaborador_id = request.form.get("colaborador_colid")
        if asodata_error:
            flash(asodata_error)
            return redirect(url_for("aso"))
        if not colaborador_id:
            flash("Colaborador e obrigatorio.")
            return redirect(url_for("aso"))
        if asoalertadias is None:
            flash("Informe uma quantidade valida de dias para alerta.")
            return redirect(url_for("aso"))
        execute("INSERT INTO Aso (Asodata, Asoalertadias, Colaborador_Colid) VALUES (%s, %s, %s)", [asodata, asoalertadias, colaborador_id])
        flash("ASO cadastrado com sucesso.")
        return redirect(url_for("aso"))
    data = fetch_all(
        """
        SELECT a.Asoid, a.Asodata, a.Asoalertadias, a.Colaborador_Colid, c.Colnome
        FROM Aso a INNER JOIN Colaborador c ON c.Colid = a.Colaborador_Colid
        ORDER BY a.Asodata ASC, a.Asoid DESC
        """
    )
    colaboradores_data = fetch_all("SELECT Colid, Colnome FROM Colaborador ORDER BY Colnome")
    return render_template("aso.html", asos=data, colaboradores=colaboradores_data)


@app.route("/aso/<int:asoid>/editar", methods=["GET", "POST"])
@login_required
def aso_editar(asoid):
    aso_data = fetch_all("SELECT Asoid, Asodata, Asoalertadias, Colaborador_Colid FROM Aso WHERE Asoid = %s", [asoid])
    if not aso_data:
        flash("ASO nao encontrado.")
        return redirect(url_for("aso"))
    if request.method == "POST":
        asodata, asodata_error = parse_date_field("asodata", "vencimento do ASO")
        asoalertadias = parse_alert_days("asoalertadias")
        colaborador_id = request.form.get("colaborador_colid")
        if asodata_error:
            flash(asodata_error)
            return redirect(url_for("aso_editar", asoid=asoid))
        if not colaborador_id:
            flash("Colaborador e obrigatorio.")
            return redirect(url_for("aso_editar", asoid=asoid))
        if asoalertadias is None:
            flash("Informe uma quantidade valida de dias para alerta.")
            return redirect(url_for("aso_editar", asoid=asoid))
        execute("UPDATE Aso SET Asodata = %s, Asoalertadias = %s, Colaborador_Colid = %s WHERE Asoid = %s", [asodata, asoalertadias, colaborador_id, asoid])
        flash("ASO atualizado com sucesso.")
        return redirect(url_for("aso"))
    colaboradores_data = fetch_all("SELECT Colid, Colnome FROM Colaborador ORDER BY Colnome")
    return render_template("aso_editar.html", aso_item=aso_data[0], colaboradores=colaboradores_data)


@app.route("/aso/<int:asoid>/excluir", methods=["POST"])
@login_required
def aso_excluir(asoid):
    execute("DELETE FROM Aso WHERE Asoid = %s", [asoid])
    flash("ASO excluido com sucesso.")
    return redirect(url_for("aso"))


@app.route("/alertas", methods=["GET", "POST"])
@login_required
def alertas_config():
    if request.method == "POST":
        updates = []
        for field, value in request.form.items():
            parts = field.split("_", 1)
            if len(parts) != 2:
                continue
            tipo, item_id = parts
            dias = parse_alert_days_value(value)
            if dias is None:
                flash("Informe apenas numeros inteiros nao negativos nos alertas.")
                return redirect(url_for("alertas_config"))
            updates.append((tipo, item_id, dias))
        conn = get_conn()
        try:
            cursor = conn.cursor()
            for tipo, item_id, dias in updates:
                if tipo == "nr":
                    cursor.execute("UPDATE Nrs SET Nrsalertadias = %s WHERE Nrsid = %s", [dias, item_id])
                elif tipo == "aso":
                    cursor.execute("UPDATE Aso SET Asoalertadias = %s WHERE Asoid = %s", [dias, item_id])
                elif tipo == "doc":
                    cursor.execute("UPDATE Documentos SET Docalertadias = %s WHERE Docid = %s", [dias, item_id])
            conn.commit()
        finally:
            conn.close()
        flash("Configuracoes de alerta atualizadas com sucesso.")
        return redirect(url_for("alertas_config"))
    nrs_data = fetch_all(
        """
        SELECT n.Nrsid, n.Nrsnumero, n.Nrsdata, n.Nrsalertadias, c.Colnome, e.Empnome
        FROM Nrs n
        INNER JOIN Colaborador c ON c.Colid = n.Colaborador_Colid
        INNER JOIN Empresa e ON e.Empid = c.Empresa_Empid
        ORDER BY n.Nrsdata ASC, n.Nrsid DESC
        """
    )
    asos_data = fetch_all(
        """
        SELECT a.Asoid, a.Asodata, a.Asoalertadias, c.Colnome, e.Empnome
        FROM Aso a
        INNER JOIN Colaborador c ON c.Colid = a.Colaborador_Colid
        INNER JOIN Empresa e ON e.Empid = c.Empresa_Empid
        ORDER BY a.Asodata ASC, a.Asoid DESC
        """
    )
    documentos_data = fetch_all(
        """
        SELECT d.Docid, d.Docnome, d.Docdt, d.Docalertadias, e.Empnome
        FROM Documentos d INNER JOIN Empresa e ON e.Empid = d.Empresa_Empid
        ORDER BY d.Docid DESC
        """
    )
    return render_template("alertas.html", nrs=nrs_data, asos=asos_data, documentos=documentos_data)


@app.route("/documentos", methods=["GET", "POST"])
@login_required
def documentos():
    if request.method == "POST":
        docnome = request.form.get("docnome", "").strip()
        docdt, docdt_error = parse_date_field("docdt", "vencimento do documento")
        docalertadias = parse_alert_days("docalertadias")
        empresa_id = request.form.get("empresa_empid")
        if docdt_error:
            flash(docdt_error)
            return redirect(url_for("documentos"))
        if not docnome or not empresa_id:
            flash("Nome do documento e empresa sao obrigatorios.")
            return redirect(url_for("documentos"))
        if docalertadias is None:
            flash("Informe uma quantidade valida de dias para alerta.")
            return redirect(url_for("documentos"))
        execute("INSERT INTO Documentos (Docnome, Docdt, Docalertadias, Empresa_Empid) VALUES (%s, %s, %s, %s)", [docnome, docdt, docalertadias, empresa_id])
        flash("Documento cadastrado com sucesso.")
        return redirect(url_for("documentos"))
    data = fetch_all(
        """
        SELECT d.Docid, d.Docnome, d.Docdt, d.Docalertadias, d.Empresa_Empid, e.Empnome
        FROM Documentos d INNER JOIN Empresa e ON e.Empid = d.Empresa_Empid
        ORDER BY d.Docid DESC
        """
    )
    empresas_data = fetch_all("SELECT Empid, Empnome FROM Empresa ORDER BY Empnome")
    return render_template("documentos.html", documentos=data, empresas=empresas_data)


@app.route("/documentos/<int:docid>/editar", methods=["GET", "POST"])
@login_required
def documentos_editar(docid):
    documento = fetch_all("SELECT Docid, Docnome, Docdt, Docalertadias, Empresa_Empid FROM Documentos WHERE Docid = %s", [docid])
    if not documento:
        flash("Documento nao encontrado.")
        return redirect(url_for("documentos"))
    if request.method == "POST":
        docnome = request.form.get("docnome", "").strip()
        docdt, docdt_error = parse_date_field("docdt", "vencimento do documento")
        docalertadias = parse_alert_days("docalertadias")
        empresa_id = request.form.get("empresa_empid")
        if docdt_error:
            flash(docdt_error)
            return redirect(url_for("documentos_editar", docid=docid))
        if not docnome or not empresa_id:
            flash("Nome do documento e empresa sao obrigatorios.")
            return redirect(url_for("documentos_editar", docid=docid))
        if docalertadias is None:
            flash("Informe uma quantidade valida de dias para alerta.")
            return redirect(url_for("documentos_editar", docid=docid))
        execute("UPDATE Documentos SET Docnome = %s, Docdt = %s, Docalertadias = %s, Empresa_Empid = %s WHERE Docid = %s", [docnome, docdt, docalertadias, empresa_id, docid])
        flash("Documento atualizado com sucesso.")
        return redirect(url_for("documentos"))
    empresas_data = fetch_all("SELECT Empid, Empnome FROM Empresa ORDER BY Empnome")
    return render_template("documentos_editar.html", documento=documento[0], empresas=empresas_data)


@app.route("/documentos/<int:docid>/excluir", methods=["POST"])
@login_required
def documentos_excluir(docid):
    execute("DELETE FROM Documentos WHERE Docid = %s", [docid])
    flash("Documento excluido com sucesso.")
    return redirect(url_for("documentos"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip()
        senha = request.form.get("senha", "")
        user = get_user_by_username(usuario)
        if not user or not user["ativo"] or not check_password_hash(user["senha_hash"], senha):
            flash("Usuario ou senha invalidos.")
            return redirect(url_for("login"))
        session.clear()
        session["user_id"] = user["id"]
        session["user_name"] = user["nome"]
        session["app_instance_token"] = APP_INSTANCE_TOKEN
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


if __name__ == "__main__":
    server_thread = threading.Thread(
        target=lambda: app.run(debug=True, use_reloader=False, port=5000),
        daemon=True,
    )
    server_thread.start()
    time.sleep(1)
    webbrowser.open("http://127.0.0.1:5000/login")
    alert_popup_once()
    server_thread.join()
