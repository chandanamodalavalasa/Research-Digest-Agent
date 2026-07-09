import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from dotenv import load_dotenv

from models import db, User, Dispatch
from research_agent import search_web, fetch_article_text, summarize_topic

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-key-change-this")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///dispatch.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to access the wire desk."

with app.app_context():
    db.create_all()

DAILY_LIMIT = 3


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("home"))

    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            error = "Both fields are required to open an account."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        elif User.query.filter_by(email=email).first():
            error = "An account already exists for that email. Try logging in instead."
        else:
            user = User(email=email)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            return redirect(url_for("home"))

    return render_template("signup.html", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("home"))

    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()

        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for("home"))
        else:
            error = "Email or password not recognized."

    return render_template("login.html", error=error)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/", methods=["GET", "POST"])
@login_required
def home():
    summary = None
    error = None
    topic = ""

    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_count = Dispatch.query.filter(
        Dispatch.user_id == current_user.id,
        Dispatch.created_at >= today_start,
    ).count()
    remaining = max(0, DAILY_LIMIT - today_count)

    if request.method == "POST":
        topic = request.form.get("topic", "").strip()

        if not topic:
            error = "No subject line entered. The desk needs a topic to dispatch a researcher."
        elif remaining <= 0:
            error = "Today's dispatch quota is spent. The wire reopens tomorrow."
        else:
            results = search_web(topic, num_results=5)
            articles = []
            for r in results:
                text = fetch_article_text(r["url"])
                articles.append({**r, "text": text})
            summary = summarize_topic(topic, articles)

            record = Dispatch(user_id=current_user.id, topic=topic, summary=summary)
            db.session.add(record)
            db.session.commit()
            remaining -= 1

    recent = (
        Dispatch.query.filter_by(user_id=current_user.id)
        .order_by(Dispatch.created_at.desc())
        .limit(3)
        .all()
    )

    return render_template(
        "index.html",
        summary=summary,
        error=error,
        topic=topic,
        remaining=remaining,
        daily_limit=DAILY_LIMIT,
        today=datetime.now().strftime("%d %b %Y").upper(),
        recent=recent,
    )


@app.route("/history")
@login_required
def history():
    dispatches = (
        Dispatch.query.filter_by(user_id=current_user.id)
        .order_by(Dispatch.created_at.desc())
        .all()
    )
    return render_template("history.html", dispatches=dispatches)


@app.route("/delete/<int:dispatch_id>", methods=["POST"])
@login_required
def delete_dispatch(dispatch_id):
    record = Dispatch.query.get_or_404(dispatch_id)

    # make sure people can only delete their own dispatches, never anyone else's
    if record.user_id != current_user.id:
        return redirect(url_for("history"))

    db.session.delete(record)
    db.session.commit()
    return redirect(url_for("history"))


@app.route("/clear-history", methods=["POST"])
@login_required
def clear_history():
    Dispatch.query.filter_by(user_id=current_user.id).delete()
    db.session.commit()
    return redirect(url_for("history"))


if __name__ == "__main__":
    app.run(debug=True)