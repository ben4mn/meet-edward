from fastapi import APIRouter
from services.usage_service import get_usage_summary

router = APIRouter()


@router.get("/usage")
async def usage_summary():
    return await get_usage_summary()
