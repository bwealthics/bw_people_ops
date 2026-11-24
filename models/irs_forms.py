# Copyright 2026 BWEALTHICS LLC
# Part of bw_people_ops. License LGPL-3.
"""Prefill helper for the IRS forms W-9 (Rev. March 2024) and
W-8BEN (Rev. October 2021). Pure Python, tested with PyPDF2 2.12.1.

SSN, TIN and foreign TIN are deliberately not mapped: the signer
types them in the signing app.
"""
import io

try:
    from PyPDF2 import PdfReader, PdfWriter
    from PyPDF2.generic import BooleanObject, NameObject, TextStringObject
except ImportError:  # newer distros ship the renamed package
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import BooleanObject, NameObject, TextStringObject

P1 = "topmostSubform[0].Page1[0]."

# Logical key -> fully qualified PDF field name.
FIELD_MAP = {
    "w9": {
        # line 1
        "name": P1 + "f1_01[0]",
        # line 5 (street)
        "address": P1 + "Address_ReadOrder[0].f1_07[0]",
        # line 6
        "city_state_zip": P1 + "Address_ReadOrder[0].f1_08[0]",
        # line 3a "Individual/sole proprietor": (field, on-value)
        "classification_individual": (
            P1 + "Boxes3a-b_ReadOrder[0].c1_1[0]",
            "/1",
        ),
    },
    "w8ben": {
        "name": P1 + "f_1[0]",  # line 1
        "citizenship_country": P1 + "f_2[0]",  # line 2
        "address": P1 + "f_3[0]",  # line 3 street
        "city_state_zip": P1 + "f_4[0]",  # line 3 city/state/postal
        "country": P1 + "f_5[0]",  # line 3 country
        "birth_date": P1 + "f_12[0]",  # line 8, MM-DD-YYYY
        "print_name": P1 + "f_21[0]",  # print name of signer
        # "I certify that I have the capacity to sign for the person
        # identified on line 1": the worker signs their own form.
        "capacity": (P1 + "c1_02[0]", "/1"),
    },
}

# Placement for Odoo Sign: page is 1-based, posX/posY from the
# TOP-LEFT of the page, all values fractions 0..1 of page size.
# Derived from AcroForm /Rect and MediaBox (W-9 611.976 x 791.968,
# W-8BEN 612 x 792.008) with posY = 1 - y_top / page_height.
SIGN_BOXES = {
    "w9": {
        # No widget exists for the signature line. Estimated from the
        # "Sign Here" row: x 76..366, y 566..581 pt from the top,
        # above the "Signature of U.S. person" label.
        "signature": {
            "page": 1, "posX": 0.1242, "posY": 0.7147,
            "width": 0.4739, "height": 0.0177,
        },
        # No widget either. Estimated on the same line, right of the
        # "Date" label (x 388..575 pt).
        "date": {
            "page": 1, "posX": 0.6340, "posY": 0.7147,
            "width": 0.3056, "height": 0.0177,
        },
        # SSN boxes: union of f1_11, f1_12, f1_13
        # (x 418..576, y_top 372..396 pt).
        "tin": {
            "page": 1, "posX": 0.6830, "posY": 0.4697,
            "width": 0.2582, "height": 0.0303,
        },
    },
    "w8ben": {
        # Signature field f_20 (a /Sig widget).
        "signature": {
            "page": 1, "posX": 0.1765, "posY": 0.8939,
            "width": 0.5180, "height": 0.0152,
        },
        # Date field (x 432..576, y 72..84 from the bottom).
        "date": {
            "page": 1, "posX": 0.7059, "posY": 0.8939,
            "width": 0.2353, "height": 0.0152,
        },
        # Line 6a foreign TIN, f_10.
        "tin": {
            "page": 1, "posX": 0.0588, "posY": 0.4848,
            "width": 0.4118, "height": 0.0152,
        },
        # Line 5 U.S. TIN (SSN or ITIN), f_9.
        "us_tin": {
            "page": 1, "posX": 0.1062, "posY": 0.4545,
            "width": 0.8355, "height": 0.0152,
        },
    },
}


def _full_name(annot):
    parts = []
    while annot is not None:
        title = annot.get("/T")
        if title is not None:
            parts.append(str(title))
        parent = annot.get("/Parent")
        annot = parent.get_object() if parent is not None else None
    return ".".join(reversed(parts))


def fill(form, pdf_bytes, values):
    """Return the unencrypted PDF with the mapped fields filled.

    form: "w9" or "w8ben"; values: dict of logical key -> value.
    Unknown keys and empty values are skipped. For checkboxes any
    truthy value checks the box.
    """
    mapping = FIELD_MAP[form]
    wanted = {}
    for key, value in (values or {}).items():
        if key in mapping and value not in (None, "", False):
            wanted[key] = value
    reader = PdfReader(io.BytesIO(pdf_bytes))
    if reader.is_encrypted:
        reader.decrypt("")
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    root = writer._root_object
    acro = root.get("/AcroForm")
    acro = acro.get_object() if acro is not None else None
    if acro is not None:
        # XFA would override the AcroForm values in Adobe viewers.
        if "/XFA" in acro:
            del acro["/XFA"]
        acro[NameObject("/NeedAppearances")] = BooleanObject(True)
    texts = {}
    checks = {}
    for key, value in wanted.items():
        target = mapping[key]
        if isinstance(target, tuple):
            checks[target[0]] = target[1]
        else:
            texts[target] = str(value)
    for page in writer.pages:
        annots = page.get("/Annots")
        annots = annots.get_object() if annots is not None else []
        leaf_values = {}
        for ref in annots:
            annot = ref.get_object()
            if annot.get("/Subtype") != "/Widget":
                continue
            name = _full_name(annot)
            if name in texts:
                leaf_values[str(annot.get("/T"))] = texts[name]
            elif name in checks:
                on = checks[name]
                annot[NameObject("/AS")] = NameObject(on)
                annot[NameObject("/V")] = NameObject(on)
                parent = annot.get("/Parent")
                if parent is not None:
                    parent.get_object()[NameObject("/V")] = NameObject(on)
        if leaf_values:
            writer.update_page_form_field_values(page, leaf_values)
            for ref in annots:
                annot = ref.get_object()
                name = _full_name(annot)
                if name in texts:
                    annot[NameObject("/V")] = TextStringObject(
                        texts[name])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


if __name__ == "__main__":
    import sys

    paths = {"w9": sys.argv[1], "w8ben": sys.argv[2]}
    samples = {
        "w9": {
            "name": "Test Worker",
            "address": "123 Example Street",
            "city_state_zip": "Springfield, TX 77000",
            "classification_individual": True,
            "bogus": "ignored",
            "empty": "",
        },
        "w8ben": {
            "name": "Test Worker",
            "citizenship_country": "Examplestan",
            "address": "1 Example Road",
            "city_state_zip": "Exampleville, EX 12345",
            "country": "Examplestan",
            "birth_date": "01-31-1990",
            "print_name": "Test Worker",
            "capacity": True,
        },
    }
    for form, path in paths.items():
        with open(path, "rb") as handle:
            data = handle.read()
        result = fill(form, data, samples[form])
        if len(sys.argv) > 3:
            with open("%s/%s_filled.pdf" % (sys.argv[3], form), "wb") as h:
                h.write(result)
        back = PdfReader(io.BytesIO(result))
        assert not back.is_encrypted
        assert "/XFA" not in back.trailer["/Root"]["/AcroForm"]
        fields = {}
        for page in back.pages:
            annots = page.get("/Annots")
            for ref in annots.get_object() if annots else []:
                annot = ref.get_object()
                fields[_full_name(annot)] = {
                    "/V": annot.get("/V"), "/AS": annot.get("/AS")}
        for key, target in FIELD_MAP[form].items():
            value = samples[form].get(key)
            if isinstance(target, tuple):
                assert fields[target[0]]["/V"] == target[1], key
                assert fields[target[0]]["/AS"] == target[1], key
            else:
                assert fields[target]["/V"] == value, (key, fields[target])
        print(form, "OK", len(result), "bytes")
