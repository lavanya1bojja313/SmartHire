import asyncio
import os
import uuid
from httpx import AsyncClient

# We must import from our local environment
from security import create_access_token
from models import Organization, User
from database import db_session

from sqlalchemy import select

async def setup_test_data():
    async with db_session() as session:
        # Check if user already exists
        result = await session.execute(select(User).where(User.email == "recruiter@test.com"))
        user = result.scalar_one_or_none()
        
        if user:
            return str(user.id), str(user.org_id)
            
        # Create an org
        org_id = uuid.uuid4()
        org = Organization(id=org_id, name="Test Org", slug="test-org")
        session.add(org)
        
        # Create a user
        user_id = uuid.uuid4()
        user = User(id=user_id, org_id=org_id, email="recruiter@test.com", name="Test Recruiter", role="recruiter")
        session.add(user)
        
        await session.commit()
        return str(user_id), str(org_id)

async def test_api():
    print("Setting up test data in the database...")
    user_id, org_id = await setup_test_data()
    
    # Generate token
    token = create_access_token(
        user_id=user_id,
        org_id=org_id,
        role="recruiter",
        email="recruiter@test.com"
    )
    print(f"Generated token for test: {token[:20]}...")
    
    async with AsyncClient(base_url="http://localhost:8000") as client:
        # 1. Create request
        headers = {"Authorization": f"Bearer {token}"}
        payload = {
            "candidate_name": "John Doe",
            "candidate_email": "john.doe@example.com",
            "position_title": "Software Engineer",
            "auto_send": False
        }
        print("\n--- POST /api/v1/requests ---")
        response = await client.post("/api/v1/requests/", json=payload, headers=headers)
        print(f"Response: {response.status_code}")
        print(response.json())
        
        if response.status_code == 201:
            req_id = response.json()["id"]
            
            # 2. Get list of requests
            print("\n--- GET /api/v1/requests ---")
            response = await client.get("/api/v1/requests/", headers=headers)
            print(f"Response: {response.status_code}")
            print(response.json())
            
            # 3. Get audit trail
            print(f"\n--- GET /api/v1/requests/{req_id}/audit ---")
            response = await client.get(f"/api/v1/requests/{req_id}/audit", headers=headers)
            print(f"Response: {response.status_code}")
            print(response.json())

if __name__ == "__main__":
    asyncio.run(test_api())
