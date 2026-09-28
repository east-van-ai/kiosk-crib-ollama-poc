FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /crib
COPY carbonara.py mcp_client.py ./

USER nobody
CMD ["python", "carbonara.py"]
