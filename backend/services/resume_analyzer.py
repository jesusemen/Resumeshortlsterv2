from emergentintegrations.llm.chat import LlmChat, UserMessage
from typing import List, Dict, Any, Optional
import json
import logging
import asyncio
import os
from datetime import datetime
import uuid

logger = logging.getLogger(__name__)

class ResumeAnalyzer:
    def __init__(self):
        self.api_key = os.getenv('GEMINI_API_KEY')
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY environment variable not set")
    
    def _create_chat_session(self) -> LlmChat:
        """Create a new Gemini chat session optimized for budget"""
        session_id = str(uuid.uuid4())
        chat = LlmChat(
            api_key=self.api_key,
            session_id=session_id,
            system_message="""You are an expert HR specialist and resume analyzer. 
            Your task is to analyze resumes against job descriptions and provide accurate, 
            concise rankings. Focus on relevance, skills match, and job requirements."""
        )
        # Use the most cost-effective model
        chat.with_model("gemini", "gemini-2.0-flash")
        chat.with_max_tokens(2048)  # Limit tokens for budget optimization
        return chat
    
    async def analyze_batch_resumes(self, job_description: str, resumes: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        Analyze multiple resumes against job description in batches for budget optimization
        """
        try:
            # Process in batches of 10 to optimize API calls
            batch_size = 10
            all_results = []
            
            for i in range(0, len(resumes), batch_size):
                batch = resumes[i:i+batch_size]
                logger.info(f"Processing batch {i//batch_size + 1} with {len(batch)} resumes")
                
                batch_results = await self._analyze_single_batch(job_description, batch)
                all_results.extend(batch_results)
            
            # Sort all results by score and take top 7
            all_results.sort(key=lambda x: x['score'], reverse=True)
            top_7 = all_results[:7]
            
            # Add rankings
            for idx, result in enumerate(top_7):
                result['rank'] = idx + 1
            
            # Check if we have any matches (threshold: 50%)
            has_matches = any(result['score'] >= 50 for result in top_7)
            
            return {
                'candidates': top_7,
                'noMatch': not has_matches,
                'total_analyzed': len(resumes),
                'analysis_date': datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error analyzing resumes: {e}")
            raise
    
    async def _analyze_single_batch(self, job_description: str, batch: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """Analyze a single batch of resumes"""
        chat = self._create_chat_session()
        
        # Create optimized prompt for batch processing
        prompt = self._create_batch_prompt(job_description, batch)
        
        user_message = UserMessage(text=prompt)
        response = await chat.send_message(user_message)
        
        # Parse the response
        return self._parse_batch_response(response, batch)
    
    def _create_batch_prompt(self, job_description: str, batch: List[Dict[str, str]]) -> str:
        """Create an optimized prompt for batch processing"""
        prompt = f"""
TASK: Analyze the following resumes against the job description and provide rankings.

JOB DESCRIPTION:
{job_description}

RESUMES TO ANALYZE:
"""
        
        for idx, resume in enumerate(batch):
            prompt += f"""
RESUME {idx + 1}:
Name: {resume['name']}
Email: {resume['email']}  
Phone: {resume['phone']}
Content: {resume['content'][:2000]}...  # Limit content for budget
---
"""
        
        prompt += """
INSTRUCTIONS:
1. Analyze each resume against the job description
2. Score each candidate from 0-100 based on:
   - Skills match (40%)
   - Experience relevance (30%)
   - Education/qualifications (20%)
   - Overall fit (10%)
3. Provide 3-5 specific reasons for each ranking
4. Extract contact information accurately

RESPONSE FORMAT (JSON):
[
  {
    "resume_number": 1,
    "score": 85,
    "reasons": ["Strong React experience", "5+ years relevant experience", "Previous similar role"]
  },
  {
    "resume_number": 2,
    "score": 72,
    "reasons": ["Basic skills match", "Limited experience", "Good potential"]
  }
]

Respond with valid JSON only, no additional text.
"""
        return prompt
    
    def _parse_batch_response(self, response: str, batch: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """Parse the AI response and create result objects"""
        try:
            # Extract JSON from response
            json_start = response.find('[')
            json_end = response.rfind(']') + 1
            json_str = response[json_start:json_end]
            
            ai_results = json.loads(json_str)
            
            results = []
            for ai_result in ai_results:
                resume_idx = ai_result['resume_number'] - 1
                if resume_idx < len(batch):
                    resume = batch[resume_idx]
                    results.append({
                        'name': resume['name'],
                        'email': resume['email'],
                        'phone': resume['phone'],
                        'score': ai_result['score'],
                        'reasons': ai_result['reasons']
                    })
            
            return results
            
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            logger.error(f"Error parsing AI response: {e}")
            # Fallback: create basic results
            return [
                {
                    'name': resume['name'],
                    'email': resume['email'],
                    'phone': resume['phone'],
                    'score': 60,  # Default score
                    'reasons': ['Unable to analyze - processing error']
                }
                for resume in batch
            ]
    
    def _extract_contact_info(self, resume_text: str) -> Dict[str, str]:
        """Extract contact information from resume text"""
        import re
        
        # Basic regex patterns for contact info
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        phone_pattern = r'(\+\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}'
        
        email_match = re.search(email_pattern, resume_text)
        phone_match = re.search(phone_pattern, resume_text)
        
        # Extract name (first line or first few words)
        lines = resume_text.split('\n')
        name = lines[0].strip() if lines else "Unknown Candidate"
        
        return {
            'name': name[:50],  # Limit name length
            'email': email_match.group() if email_match else "email@example.com",
            'phone': phone_match.group() if phone_match else "+1-555-000-0000"
        }