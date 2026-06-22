FROM python:3.11-slim

# Set the working directory
WORKDIR /code

# Copy the requirements file and install dependencies
COPY ./backend/requirements.txt /code/requirements.txt
RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt

# Copy the backend code into the container
COPY ./backend /code/

# Hugging Face Spaces exposes port 7860 by default
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
