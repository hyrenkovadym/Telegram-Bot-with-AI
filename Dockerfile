FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -U pip setuptools wheel \
 && pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

# опційно
EXPOSE 8080

CMD sh -c '\
  if [ -n "$GOOGLE_CREDENTIALS_JSON" ]; then printf "%s" "$GOOGLE_CREDENTIALS_JSON" > frendt-service.json; fi; \
  if [ -n "$BLACKLIST_PHONES" ]; then printf "%s" "$BLACKLIST_PHONES" | tr "," "\n" > blacklist_phones.txt; fi; \
  python frendt_bot.py \
'
