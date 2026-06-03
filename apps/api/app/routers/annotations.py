"""
Anotações na timeline — o gestor marca eventos no gráfico de linha do dashboard
("Black Friday", "mudamos o checkout", "lançamos promoção de inverno") para
explicar variações nos dados.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..database import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/annotations", tags=["annotations"])


def _resolve(pixel_id: str) -> Optional[str]:
    r = (get_supabase().table("clients").select("id").eq("pixel_id", pixel_id).limit(1).execute())
    return r.data[0]["id"] if (r and r.data) else None


class AnnotationBody(BaseModel):
    date:  str          # YYYY-MM-DD
    label: str


@router.get("/{pixel_id}", summary="Lista anotações da timeline")
async def list_annotations(pixel_id: str, start: Optional[str] = None, end: Optional[str] = None):
    cid = _resolve(pixel_id)
    if not cid:
        raise HTTPException(404, "client not found")
    q = (get_supabase().table("timeline_annotations")
         .select("id, date, label, created_at")
         .eq("client_id", cid).order("date", desc=False))
    if start:
        q = q.gte("date", start)
    if end:
        q = q.lte("date", end)
    return {"annotations": q.limit(500).execute().data or []}


@router.post("/{pixel_id}", summary="Cria uma anotação")
async def create_annotation(pixel_id: str, body: AnnotationBody):
    cid = _resolve(pixel_id)
    if not cid:
        raise HTTPException(404, "client not found")
    label = (body.label or "").strip()
    date  = (body.date or "").strip()
    if not label or not date:
        raise HTTPException(400, "date e label são obrigatórios")
    res = (get_supabase().table("timeline_annotations")
           .insert({"client_id": cid, "date": date, "label": label[:120]})
           .execute())
    return (res.data or [{}])[0]


@router.delete("/{pixel_id}/{annotation_id}", summary="Remove uma anotação")
async def delete_annotation(pixel_id: str, annotation_id: str):
    cid = _resolve(pixel_id)
    if not cid:
        raise HTTPException(404, "client not found")
    (get_supabase().table("timeline_annotations")
     .delete().eq("id", annotation_id).eq("client_id", cid).execute())
    return {"deleted": True}
