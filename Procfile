# Comando de migração automática antes de subir a aplicação (descomente conforme a plataforma):
# Railway / Heroku (release phase):
# release: alembic upgrade head
#
# Render (Pre-Deploy Command no painel ou encadeado no startCommand):
# web: alembic upgrade head && gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT

web: gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT
