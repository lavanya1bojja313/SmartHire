import asyncio
import asyncpg

async def reset():
    conn = await asyncpg.connect('postgresql://postgres:postgres@db:5432/interview_scheduler')
    await conn.execute('DROP TYPE IF EXISTS user_role_enum CASCADE;')
    await conn.execute('DROP TYPE IF EXISTS scheduling_state_enum CASCADE;')
    await conn.execute('DROP TYPE IF EXISTS calendar_provider_enum CASCADE;')
    await conn.execute('DROP TABLE IF EXISTS alembic_version CASCADE;')
    await conn.close()
    print("Types reset complete.")

if __name__ == "__main__":
    asyncio.run(reset())
