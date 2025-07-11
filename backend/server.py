from fastapi import FastAPI, APIRouter, UploadFile, File, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional
import uuid
from datetime import datetime
import asyncio

# Import custom services
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent))

from services.document_parser import DocumentParser
from services.resume_analyzer import ResumeAnalyzer

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Create the main app
app = FastAPI()

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# Initialize services
document_parser = DocumentParser()
resume_analyzer = ResumeAnalyzer()

# Models
class AnalysisResult(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    job_description_filename: str
    total_resumes: int
    candidates: List[dict]
    no_match: bool
    analysis_date: datetime = Field(default_factory=datetime.utcnow)

class StatusCheck(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class StatusCheckCreate(BaseModel):
    client_name: str

# Existing routes
@api_router.get("/")
async def root():
    return {"message": "Resume Analyzer API Ready"}

@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(input: StatusCheckCreate):
    status_dict = input.dict()
    status_obj = StatusCheck(**status_dict)
    _ = await db.status_checks.insert_one(status_obj.dict())
    return status_obj

@api_router.get("/status", response_model=List[StatusCheck])
async def get_status_checks():
    status_checks = await db.status_checks.find().to_list(1000)
    return [StatusCheck(**status_check) for status_check in status_checks]

# New resume analysis endpoint
@api_router.post("/analyze-resumes")
async def analyze_resumes(
    job_description: UploadFile = File(...),
    resumes: List[UploadFile] = File(...)
):
    """
    Analyze resumes against job description
    """
    try:
        # Validate inputs
        if not job_description.filename.lower().endswith(('.pdf', '.doc', '.docx')):
            raise HTTPException(
                status_code=400, 
                detail="Job description must be a PDF, DOC, or DOCX file"
            )
        
        if len(resumes) < 30:
            raise HTTPException(
                status_code=400, 
                detail="At least 30 resumes are required for analysis"
            )
        
        # Validate resume file types
        for resume in resumes:
            if not resume.filename.lower().endswith(('.pdf', '.doc', '.docx')):
                raise HTTPException(
                    status_code=400, 
                    detail=f"Resume {resume.filename} must be a PDF, DOC, or DOCX file"
                )
        
        # Extract job description text
        job_content = await job_description.read()
        job_text = document_parser.extract_text(job_content, job_description.filename)
        
        if not job_text:
            raise HTTPException(
                status_code=400, 
                detail="Could not extract text from job description"
            )
        
        # Process resumes
        resume_data = []
        for resume in resumes:
            resume_content = await resume.read()
            resume_text = document_parser.extract_text(resume_content, resume.filename)
            
            if resume_text:
                # Extract contact info from resume
                contact_info = resume_analyzer._extract_contact_info(resume_text)
                resume_data.append({
                    'name': contact_info['name'],
                    'email': contact_info['email'],
                    'phone': contact_info['phone'],
                    'content': resume_text,
                    'filename': resume.filename
                })
        
        logging.info(f"Processing {len(resume_data)} valid resumes")
        
        if len(resume_data) < 30:
            raise HTTPException(
                status_code=400, 
                detail=f"Only {len(resume_data)} resumes could be processed. At least 30 are required."
            )
        
        # Analyze resumes using AI
        analysis_results = await resume_analyzer.analyze_batch_resumes(job_text, resume_data)
        
        # Save results to database
        result_obj = AnalysisResult(
            job_description_filename=job_description.filename,
            total_resumes=len(resume_data),
            candidates=analysis_results['candidates'],
            no_match=analysis_results['noMatch']
        )
        
        await db.analysis_results.insert_one(result_obj.dict())
        
        return {
            "success": True,
            "message": "Analysis completed successfully",
            "data": {
                "candidates": analysis_results['candidates'],
                "noMatch": analysis_results['noMatch'],
                "totalAnalyzed": len(resume_data),
                "analysisDate": result_obj.analysis_date.isoformat()
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error analyzing resumes: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )

# Get analysis history
@api_router.get("/analysis-history")
async def get_analysis_history():
    """Get past analysis results"""
    try:
        results = await db.analysis_results.find().sort("analysis_date", -1).to_list(10)
        return [
            {
                "id": result["id"],
                "job_description_filename": result["job_description_filename"],
                "total_resumes": result["total_resumes"],
                "analysis_date": result["analysis_date"],
                "candidates_count": len(result["candidates"])
            }
            for result in results
        ]
    except Exception as e:
        logging.error(f"Error fetching analysis history: {e}")
        raise HTTPException(status_code=500, detail="Error fetching analysis history")

# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)