FROM ghcr.io/developmentseed/titiler:latest

# Render sets $PORT. Use TiTiler's settings to allow browser access.
ENV TITILER_API_CORS_ORIGINS=*
ENV TITILER_API_CORS_ALLOW_METHODS=GET
ENV TITILER_API_CACHECONTROL="public, max-age=3600"

EXPOSE 8000

CMD ["sh", "-c", "uvicorn titiler.application.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

