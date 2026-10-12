from sqlalchemy import select

from models import Settings, SettingsUpdate
from services.database import async_session, SettingsModel
from services.timezone_utils import default_timezone, set_user_tz


def _model_to_pydantic(db_settings: SettingsModel) -> Settings:
    return Settings(
        name=db_settings.name,
        personality=db_settings.personality,
        provider=db_settings.provider or "anthropic",
        model=db_settings.model,
        temperature=db_settings.temperature,
        system_prompt=db_settings.system_prompt,
        debug_streaming=bool(getattr(db_settings, "debug_streaming", False) or False),
        timezone=getattr(db_settings, "timezone", None) or default_timezone(),
    )


async def get_settings() -> Settings:
    """Retrieve current settings from database."""
    async with async_session() as session:
        result = await session.execute(
            select(SettingsModel).where(SettingsModel.id == "default")
        )
        db_settings = result.scalar_one_or_none()

        if db_settings:
            return _model_to_pydantic(db_settings)

        # Return defaults if no settings found
        return Settings()

async def update_settings(settings_update: SettingsUpdate) -> Settings:
    """Update settings in database."""
    async with async_session() as session:
        result = await session.execute(
            select(SettingsModel).where(SettingsModel.id == "default")
        )
        db_settings = result.scalar_one_or_none()

        if not db_settings:
            db_settings = SettingsModel(id="default")
            session.add(db_settings)

        # Update only provided fields (except name, which is fixed)
        update_data = settings_update.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            if field == "name":
                continue  # Edward's identity is fixed
            if value is not None:
                setattr(db_settings, field, value)

        await session.commit()
        await session.refresh(db_settings)

        # Propagate timezone change to the in-process cache so the scheduler,
        # heartbeat, and next chat turn see it without a restart.
        tz_value = getattr(db_settings, "timezone", None)
        if tz_value:
            set_user_tz(tz_value)

        return _model_to_pydantic(db_settings)
