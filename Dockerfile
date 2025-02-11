# Python image to use.
#FROM python:3.12-alpine

# Set the working directory to /app
#WORKDIR /app

# copy the requirements file used for dependencies
#COPY requirements.txt .

# Install any needed packages specified in requirements.txt
#RUN pip install --trusted-host pypi.python.org -r requirements.txt

# Copy the rest of the working directory contents into the container at /app
#COPY . .




# Final stage
FROM python:3.9.18-slim

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Set environment variable for OpenAI API key
ARG OPENAI_API_KEY
ENV OPENAI_API_KEY=${OPENAI_API_KEY}

EXPOSE 8080

CMD ["sh", "-c", "streamlit run app.py --server.port $PORT --server.address 0.0.0.0"]


# Set environment variable for OpenAI API key
#ARG OPENAI_API_KEY
#ENV OPENAI_API_KEY=${OPENAI_API_KEY}

