# Obraz do lokalnego uruchamiania testów (Home Assistant nie działa na Windows).
FROM python:3.13
COPY requirements_test.txt /tmp/requirements_test.txt
RUN pip install --no-cache-dir -q -r /tmp/requirements_test.txt
WORKDIR /src
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
