"""Shared Office styling tokens for generated Word documents."""
from pathlib import Path
def apply(doc,title):
    from docx.shared import Inches,Pt,RGBColor
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    styles=list(doc.styles)
    paragraph_names={style.style_id:style.name for style in styles if style.type==1}
    default_name=doc.styles.default(1).name
    for style in styles:
        if style.type==1:
            style.font.name='Arial'
            style.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'DengXian')
            style.font.color.rgb=RGBColor.from_string('1F1F1F' if style.name.startswith(('Heading','Title')) else '1A1A1A')
    for p in list(doc.paragraphs)+[p for t in doc.tables for c in t._cells for p in c.paragraphs]:
        # Resolving p.style repeatedly scans every document style for the default.
        # Cache names once; missing/invalid paragraph style IDs still use the default.
        name=paragraph_names.get(p._p.style,default_name)
        color=RGBColor.from_string('1F1F1F' if name.startswith(('Heading','Title')) else '1A1A1A')
        for run in p.runs:
            run.font.name='Arial';run._r.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'DengXian')
            run.font.color.rgb=color
    sec=doc.sections[0];sec.different_first_page_header_footer=False;sec.header_distance=Inches(.25);sec.footer_distance=Inches(.25)
    sec.top_margin=Inches(.65);sec.bottom_margin=Inches(.6)
    logo=Path(__file__).resolve().parents[1]/'assets/brand-mark.png'
    if logo.exists():
        p=sec.header.paragraphs[0];p.alignment=2;p.add_run().add_picture(str(logo),width=Inches(.65))
    p=sec.footer.paragraphs[0];r=p.add_run(title+'  |  ');r.font.size=Pt(8);r.font.color.rgb=RGBColor.from_string('888888')
    field=OxmlElement('w:fldSimple');field.set(qn('w:instr'),'PAGE');p._p.append(field)
