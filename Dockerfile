FROM python:3.11-slim-bookworm

# Install required system tools for disk management, formatting and testing
RUN apt-get update && apt-get install -y \
    smartmontools \
    fio \
    parted \
    e2fsprogs \
    dosfstools \
    ntfs-3g \
    util-linux \
    udev \
    psmisc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install Python dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend and frontend files
COPY backend /app/backend
COPY frontend /app/frontend

# Set environment variables for Flask
ENV FLASK_APP=backend/app.py
ENV FLASK_RUN_HOST=0.0.0.0
ENV FLASK_RUN_PORT=5000

# Expose port
EXPOSE 5000

# Start the Flask app
CMD ["flask", "run"]
