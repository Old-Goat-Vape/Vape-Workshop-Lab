from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
import os
from datetime import datetime

def generate_monthly_pdf(logs, filename, title):
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    c = canvas.Canvas(filename, pagesize=A4)
    c.setFont("Helvetica", 10)
    y = 800

    c.drawString(50, y, title)
    y -= 30

    for log in logs:
        line = " | ".join(str(v) for v in log)
        c.drawString(50, y, line)
        y -= 15
        if y < 50:
            c.showPage()
            y = 800

    c.save()
