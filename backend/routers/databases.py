"""REST API endpoints for persistent databases."""

from fastapi import APIRouter, HTTPException

from services.persistent_db_service import (
    list_databases,
    get_database,
    get_tables,
    get_columns,
    delete_database,
)

router = APIRouter()


@router.get("/databases")
async def list_all_databases():
    """List all persistent databases."""
    return await list_databases()


@router.get("/databases/{name}")
async def get_single_database(name: str):
    """Get a single persistent database by name."""
    db = await get_database(name)
    if not db:
        raise HTTPException(status_code=404, detail="Database not found")
    return db


@router.get("/databases/{name}/tables")
async def get_database_tables(name: str):
    """Get tables in a persistent database."""
    db = await get_database(name)
    if not db:
        raise HTTPException(status_code=404, detail="Database not found")
    return await get_tables(name)


@router.get("/databases/{name}/tables/{table_name}/columns")
async def get_table_columns(name: str, table_name: str):
    """Get columns for a table in a persistent database."""
    db = await get_database(name)
    if not db:
        raise HTTPException(status_code=404, detail="Database not found")
    return await get_columns(name, table_name)


@router.delete("/databases/{name}")
async def remove_database(name: str):
    """Delete a persistent database and all its data."""
    deleted = await delete_database(name)
    if not deleted:
        raise HTTPException(status_code=404, detail="Database not found")
    return {"status": "deleted", "name": name}
