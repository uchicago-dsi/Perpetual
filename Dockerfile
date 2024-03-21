# Pull base image
FROM jupyter/minimal-notebook:python-3.11

# Switch to root to update package manager and install Python dev tools
USER root
RUN apt update
RUN apt install -y python3-pip python3-dev

# Switch to NB_UID user to install additional packages
USER $NB_UID

# Create working directory
WORKDIR /perpetual

# Install Python packages
COPY requirements.txt .
# COPY requirements_arcgis.txt .
RUN pip install --no-cache-dir -r requirements.txt
# RUN pip install --no-cache-dir -r requirements_arcgis.txt

# Install project as an editable package
COPY pipeline ./pipeline
COPY setup.py .
RUN pip install -e .

# Run container and expose an interactive bash shell
CMD ["/bin/bash"]