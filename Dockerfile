FROM python:3.12-slim

WORKDIR /app

# Install pinemcp from source (includes REST adapter endpoints)
COPY . /app/
RUN pip install --no-cache-dir -e ".[pipeline]"

# Pre-build ChromaDB at image build time (avoids 30-60s startup delay)
RUN pinemcp build

EXPOSE 8080

CMD ["pinemcp", "--transport", "sse", "--port", "8080"]
