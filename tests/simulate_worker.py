"""Имитируем то, что сделает воркер Кирилла: записать поля и статус."""
import asyncio, sys
from sqlalchemy import select
from app.db import session_factory, engine
from app.enums import DocStatus, DocType, FieldSource
from app.models import Application, Document, Field
from app.model.validate import validate_document, normalize_fields

TOKEN = sys.argv[1]

async def main():
    async with session_factory() as s:
        app = await s.scalar(select(Application).where(Application.applicant_token == TOKEN))
        doc = await s.scalar(select(Document).where(
            Document.application_id == app.id, Document.doc_type == DocType.PASSPORT_UZ))

        raw = {"number": "AA1234567", "full_name": "KARIMOV AZIZ ANVAROVICH",
               "birth_date": "05.03.1988", "expiry_date": "01.01.2020"}
        fields = normalize_fields(DocType.PASSPORT_UZ, raw)
        report = validate_document(DocType.PASSPORT_UZ, fields)

        msgs = {c.name: c.message for c in report.fields}
        for name, value in fields.items():
            s.add(Field(document_id=doc.id, name=name, value=value,
                        source=FieldSource.MODEL,
                        is_valid=msgs.get(name) is None,
                        validation_msg=msgs.get(name)))
        doc.status = report.status
        doc.error_reason = "; ".join(report.notes) or None
        await s.commit()
        print("статус документа:", report.status.value, "| замечания:", report.notes)

asyncio.run(main())
