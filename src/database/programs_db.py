"""
Programs Database Module
Loads and queries university program information from Excel
"""
import logging
from pathlib import Path
from typing import Dict, List, Optional
import pandas as pd

logger = logging.getLogger(__name__)


class ProgramsDatabase:
    """Database for university programs information"""
    
    def __init__(self, excel_path: Optional[str] = None):
        """
        Initialize Programs Database
        
        Args:
            excel_path: Path to the Excel file with program data
        """
        self.excel_path = excel_path
        self.programs_df = None
        
        if excel_path:
            self.load_data(excel_path)
    
    def load_data(self, excel_path: str):
        """Load program data from Excel file"""
        try:
            self.programs_df = pd.read_excel(excel_path)
            # Clean column names
            self.programs_df.columns = [col.strip() for col in self.programs_df.columns]
            logger.info(f"Loaded {len(self.programs_df)} programs from {excel_path}")
        except Exception as e:
            logger.error(f"Failed to load Excel data: {e}")
            raise
    
    def get_all_programs(self) -> List[Dict]:
        """Get all programs as list of dictionaries"""
        if self.programs_df is None:
            return []
        return self.programs_df.to_dict('records')
    
    def get_programs_by_level(self, level: str) -> List[Dict]:
        """
        Get programs by level (Undergraduate, Graduate, Doctoral)
        
        Args:
            level: Program level
            
        Returns:
            List of matching programs
        """
        if self.programs_df is None:
            return []
        
        filtered = self.programs_df[
            self.programs_df['Program Level'].str.lower() == level.lower()
        ]
        return filtered.to_dict('records')
    
    def search_program(self, query: str) -> List[Dict]:
        """
        Search programs by name
        
        Args:
            query: Search query
            
        Returns:
            List of matching programs
        """
        if self.programs_df is None:
            return []
        
        query_lower = query.lower()
        filtered = self.programs_df[
            self.programs_df['Program Name'].str.lower().str.contains(query_lower, na=False)
        ]
        return filtered.to_dict('records')
    
    def get_program_by_name(self, name: str) -> Optional[Dict]:
        """
        Get specific program by exact name
        
        Args:
            name: Program name
            
        Returns:
            Program info or None
        """
        results = self.search_program(name)
        return results[0] if results else None
    
    def get_fee_info(self, program_name: str) -> Optional[str]:
        """
        Get fee information for a program
        
        Args:
            program_name: Program name to search
            
        Returns:
            Fee information string
        """
        program = self.get_program_by_name(program_name)
        if program:
            fee_per_credit = program.get('Tuition Per Credit Hour (PKR)', 'N/A')
            semester_fee = program.get('Estimated First Semester Tuition (PKR)', 'N/A')
            return f"فی کریڈٹ آور: {fee_per_credit} روپے، پہلے سمسٹر کی تخمینی فیس: {semester_fee} روپے"
        return None
    
    def get_eligibility_info(self, program_name: str) -> Optional[str]:
        """
        Get eligibility information for a program
        
        Args:
            program_name: Program name to search
            
        Returns:
            Eligibility information string
        """
        program = self.get_program_by_name(program_name)
        if program:
            return program.get('Eligibility Summary', None)
        return None
    
    def format_program_info_urdu(self, program: Dict) -> str:
        """Format program info in Urdu"""
        name = program.get('Program Name', '')
        level = program.get('Program Level', '')
        fee = program.get('Tuition Per Credit Hour (PKR)', '')
        duration = program.get('Duration (Years)', '')
        eligibility = program.get('Eligibility Summary', '')
        
        return (
            f"پروگرام: {name}\n"
            f"سطح: {level}\n"
            f"فیس: {fee} روپے فی کریڈٹ آور\n"
            f"مدت: {duration} سال\n"
            f"اہلیت: {eligibility}"
        )
    
    def format_program_info_english(self, program: Dict) -> str:
        """Format program info in English"""
        name = program.get('Program Name', '')
        level = program.get('Program Level', '')
        fee = program.get('Tuition Per Credit Hour (PKR)', '')
        duration = program.get('Duration (Years)', '')
        eligibility = program.get('Eligibility Summary', '')
        
        return (
            f"Program: {name}\n"
            f"Level: {level}\n"
            f"Fee: PKR {fee} per credit hour\n"
            f"Duration: {duration} years\n"
            f"Eligibility: {eligibility}"
        )
    
    def list_program_names(self) -> List[str]:
        """Get list of all program names"""
        if self.programs_df is None:
            return []
        return self.programs_df['Program Name'].tolist()

