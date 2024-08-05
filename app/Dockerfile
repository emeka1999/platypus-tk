# Use the base image
FROM zauberzeug/nicegui

# Set the working directory
WORKDIR /app

# Copy the requirements file to the working directory
COPY requirements.txt .

# Install the Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install pytest
RUN pip install pytest
RUN pip install pywebview

# Install dependencies for GTK and QT
RUN apt-get update && \
    apt-get install -y \
    libgtk-3-dev \
    libgirepository1.0-dev \
    gir1.2-webkit2-4.0 \
    python3-gi \
    python3-gi-cairo \
    qtbase5-dev \
    qtchooser \
    qt5-qmake \
    qtbase5-dev-tools \
    pyqt5-dev-tools \
    python3-pyqt5 \ 
    xvfb \ 
    xorg

# Install Python packages for GTK and QT
RUN pip install PyGObject qtpy

# Copy the application code to the working directory
COPY . .

# Create the necessary directory with the correct permissions
RUN mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix


# Specify the command to run the application
# Run Xvfb and then the application
CMD ["sh", "-c", "Xvfb :99 -screen 0 1024x768x24 & export DISPLAY=:99 && python main.py"]

