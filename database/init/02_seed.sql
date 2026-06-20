INSERT INTO departments VALUES
('DEP-ED','Emergency Department','Urgent and Emergency Care','Level 1, East Wing','020-5555-1001','ed@example.nhs','Dr Marcus Reed','Duty Manager 020-5555-1505','all_staff'),
('DEP-CARD','Cardiology','Medicine','Level 3, North Wing','020-5555-1002','cardiology@example.nhs','Dr Aisha Malik','Cardiology Registrar Bleep 2301','clinical'),
('DEP-RESP','Respiratory','Medicine','Level 4, North Wing','020-5555-1003','respiratory@example.nhs','Dr Laura Evans','Respiratory Registrar Bleep 2302','clinical'),
('DEP-ICU','Intensive Care Unit','Critical Care','Level 2, West Wing','020-5555-1004','icu@example.nhs','Dr Helen Carter','ICU Outreach 020-5555-1101','clinical'),
('DEP-PHAR','Pharmacy','Medicines Management','Level 0, South Wing','020-5555-1005','pharmacy@example.nhs','Priya Shah','Pharmacy Lead 020-5555-1202','all_staff'),
('DEP-MAT','Maternity','Women and Children','Level 5, Maternity Block','020-5555-1006','maternity@example.nhs','Grace Morgan','Obstetric Emergency Bleep 2401','clinical'),
('DEP-PAED','Paediatrics','Women and Children','Level 5, Children Block','020-5555-1007','paediatrics@example.nhs','Dr Omar Hussain','Paediatric Registrar Bleep 2402','clinical'),
('DEP-ONC','Oncology','Cancer Services','Level 6, North Wing','020-5555-1008','oncology@example.nhs','Dr Emily Turner','Oncology Hotline 020-5555-1808','clinical'),
('DEP-RAD','Radiology','Diagnostics','Level 0, Imaging Suite','020-5555-1009','radiology@example.nhs','Dr James Wilson','Urgent Radiology Bleep 2601','clinical'),
('DEP-HR','Human Resources','Corporate Services','Admin Block, Floor 2','020-5555-1010','hr@example.nhs','Sofia Grant','HR Advice 020-5555-1404','hr_manager')
ON CONFLICT (department_id) DO NOTHING;

INSERT INTO doctors VALUES
('DOC-001','Dr Aisha Malik','Consultant Physician','Cardiology','DEP-CARD','Cardiology','020-5555-2101','aisha.malik@example.nhs','2301',true,'clinical'),
('DOC-002','Dr Marcus Reed','Consultant Physician','Emergency Medicine','DEP-ED','Emergency Department','020-5555-2102','marcus.reed@example.nhs','2201',true,'clinical'),
('DOC-003','Dr Helen Carter','Consultant Intensivist','Critical Care','DEP-ICU','Intensive Care Unit','020-5555-2103','helen.carter@example.nhs','2501',true,'clinical'),
('DOC-004','Dr Laura Evans','Respiratory Consultant','Respiratory','DEP-RESP','Respiratory','020-5555-2104','laura.evans@example.nhs','2302',false,'clinical'),
('DOC-005','Dr Omar Hussain','Paediatric Consultant','Paediatrics','DEP-PAED','Paediatrics','020-5555-2105','omar.hussain@example.nhs','2402',true,'clinical'),
('DOC-006','Dr Emily Turner','Oncology Consultant','Oncology','DEP-ONC','Oncology','020-5555-2106','emily.turner@example.nhs','2801',false,'clinical'),
('DOC-007','Dr James Wilson','Radiology Consultant','Radiology','DEP-RAD','Radiology','020-5555-2107','james.wilson@example.nhs','2601',true,'clinical'),
('DOC-008','Dr Fatima Khan','Obstetric Consultant','Maternity','DEP-MAT','Maternity','020-5555-2108','fatima.khan@example.nhs','2401',true,'clinical'),
('DOC-009','Dr Ravi Singh','Medical Registrar','General Medicine','DEP-ED','Emergency Department','020-5555-2109','ravi.singh@example.nhs','2202',false,'clinical'),
('DOC-010','Dr Hannah Lewis','Cardiology Registrar','Cardiology','DEP-CARD','Cardiology','020-5555-2110','hannah.lewis@example.nhs','2303',false,'clinical'),
('DOC-011','Dr Yusuf Ahmed','ICU Registrar','Critical Care','DEP-ICU','Intensive Care Unit','020-5555-2111','yusuf.ahmed@example.nhs','2502',false,'clinical'),
('DOC-012','Dr Chloe Ward','Respiratory Registrar','Respiratory','DEP-RESP','Respiratory','020-5555-2112','chloe.ward@example.nhs','2304',true,'clinical')
ON CONFLICT (doctor_id) DO NOTHING;

INSERT INTO wards VALUES
('W01','Emergency Assessment Unit','DEP-ED','Emergency Department','1',28,4,'Daniel Price','020-6666-2201','all_staff'),
('W02','Cardiology Ward A','DEP-CARD','Cardiology','3',24,3,'Mina Patel','020-6666-2202','all_staff'),
('W03','Respiratory Ward B','DEP-RESP','Respiratory','4',26,5,'Nadia Ali','020-6666-2203','all_staff'),
('W04','Intensive Care Unit','DEP-ICU','Intensive Care Unit','2',18,1,'Grace Morgan','020-6666-2204','clinical'),
('W05','Pharmacy Medicines Unit','DEP-PHAR','Pharmacy','0',8,2,'Priya Shah','020-6666-2205','all_staff'),
('W06','Maternity Triage','DEP-MAT','Maternity','5',16,4,'Ella Cooper','020-6666-2206','all_staff'),
('W07','Paediatric Assessment Unit','DEP-PAED','Paediatrics','5',20,3,'Lucy Hall','020-6666-2207','all_staff'),
('W08','Oncology Day Unit','DEP-ONC','Oncology','6',22,6,'Maya Roberts','020-6666-2208','clinical'),
('W09','Radiology Recovery','DEP-RAD','Radiology','0',10,2,'Ben Morris','020-6666-2209','clinical'),
('W10','HR Occupational Health Suite','DEP-HR','Human Resources','Admin 2',6,1,'Sofia Grant','020-6666-2210','hr_manager')
ON CONFLICT (ward_code) DO NOTHING;

INSERT INTO patients VALUES
('PAT-001','MRN10001','9000000001','John Spencer','1958-04-14','W02','DEP-CARD','Cardiology','Dr Aisha Malik','Inpatient','Falls risk','clinical'),
('PAT-002','MRN10002','9000000002','Mary Collins','1971-09-22','W03','DEP-RESP','Respiratory','Dr Laura Evans','Inpatient','Oxygen therapy','clinical'),
('PAT-003','MRN10003','9000000003','Ahmed Rahman','1984-01-05','W04','DEP-ICU','Intensive Care Unit','Dr Helen Carter','Critical care','Sepsis watch','clinical'),
('PAT-004','MRN10004','9000000004','Susan Walker','1949-11-30','W01','DEP-ED','Emergency Department','Dr Marcus Reed','Assessment','High NEWS2','clinical'),
('PAT-005','MRN10005','9000000005','Patricia Young','1992-03-18','W06','DEP-MAT','Maternity','Dr Fatima Khan','Maternity review','Postpartum observation','clinical'),
('PAT-006','MRN10006','9000000006','Leo Bennett','2018-07-02','W07','DEP-PAED','Paediatrics','Dr Omar Hussain','Inpatient','Paediatric observation','clinical'),
('PAT-007','MRN10007','9000000007','Robert Green','1966-12-12','W08','DEP-ONC','Oncology','Dr Emily Turner','Day case','Neutropenic risk','clinical'),
('PAT-008','MRN10008','9000000008','Linda Hughes','1955-06-25','W09','DEP-RAD','Radiology','Dr James Wilson','Recovery','Contrast reaction history','clinical'),
('PAT-009','MRN10009','9000000009','George Clarke','1978-10-09','W02','DEP-CARD','Cardiology','Dr Hannah Lewis','Inpatient','Anticoagulation','clinical'),
('PAT-010','MRN10010','9000000010','Maya Roberts','1989-05-16','W03','DEP-RESP','Respiratory','Dr Chloe Ward','Inpatient','Isolation precautions','clinical'),
('PAT-011','MRN10011','9000000011','Thomas Green','1961-02-28','W04','DEP-ICU','Intensive Care Unit','Dr Yusuf Ahmed','Critical care','Ventilated','clinical'),
('PAT-012','MRN10012','9000000012','Ella Cooper','2001-08-04','W01','DEP-ED','Emergency Department','Dr Ravi Singh','Assessment','Safeguarding note','clinical')
ON CONFLICT (patient_id) DO NOTHING;

INSERT INTO organization_contacts VALUES
('CON-001','Clinical escalation','DEP-ICU','Intensive Care Unit','ICU Outreach','ICU Outreach Team','020-5555-1101','icu.outreach@example.nhs','24/7','urgent','clinical'),
('CON-002','Medication safety','DEP-PHAR','Pharmacy','Pharmacy Lead','Chief Pharmacist Office','020-5555-1202','pharmacy.lead@example.nhs','08:00-20:00','urgent','clinical'),
('CON-003','Data breach','DEP-ED','Emergency Department','Information Governance Helpdesk','Information Governance','020-5555-1303','ig@example.nhs','24/7 urgent line','urgent','all_staff'),
('CON-004','Staff absence','DEP-HR','Human Resources','HR Advice','HR Advisory Team','020-5555-1404','hr.advice@example.nhs','09:00-17:00','routine','hr_manager'),
('CON-005','Duty manager','DEP-ED','Emergency Department','Duty Manager','Site Operations','020-5555-1505','duty.manager@example.nhs','24/7','urgent','all_staff'),
('CON-006','Safeguarding','DEP-PAED','Paediatrics','Safeguarding Lead','Safeguarding Team','020-5555-1606','safeguarding@example.nhs','24/7 urgent line','urgent','all_staff'),
('CON-007','Obstetric emergency','DEP-MAT','Maternity','Obstetric Emergency Team','Maternity Emergency Response','020-5555-1707','obs.emergency@example.nhs','24/7','urgent','clinical'),
('CON-008','Oncology hotline','DEP-ONC','Oncology','Oncology Hotline','Acute Oncology Service','020-5555-1808','oncology.hotline@example.nhs','08:00-22:00','urgent','clinical'),
('CON-009','Radiology urgent report','DEP-RAD','Radiology','Urgent Radiology Desk','Radiology Coordinator','020-5555-1909','urgent.radiology@example.nhs','08:00-20:00','urgent','clinical'),
('CON-010','Cardiology advice','DEP-CARD','Cardiology','Cardiology Registrar','Cardiology On-call','020-5555-1910','cardiology.oncall@example.nhs','24/7','urgent','clinical'),
('CON-011','Respiratory advice','DEP-RESP','Respiratory','Respiratory Registrar','Respiratory On-call','020-5555-1911','respiratory.oncall@example.nhs','24/7','urgent','clinical'),
('CON-012','Occupational health','DEP-HR','Human Resources','Occupational Health','Occupational Health Team','020-5555-1912','occupational.health@example.nhs','09:00-17:00','routine','hr_manager')
ON CONFLICT (contact_id) DO NOTHING;

INSERT INTO appointments VALUES
('APT-001','MRN10001','John Spencer','Cardiology Follow-up','DEP-CARD','Cardiology','2026-06-24','09:00','Dr Aisha Malik','Booked','Routine','clinical'),
('APT-002','MRN10002','Mary Collins','Respiratory Review','DEP-RESP','Respiratory','2026-06-24','10:30','Dr Laura Evans','Booked','Urgent','clinical'),
('APT-003','MRN10003','Ahmed Rahman','ICU Stepdown Review','DEP-ICU','Intensive Care Unit','2026-06-25','11:00','Dr Helen Carter','Booked','Urgent','clinical'),
('APT-004','MRN10004','Susan Walker','ED Safety Net Clinic','DEP-ED','Emergency Department','2026-06-25','14:00','Dr Marcus Reed','Booked','Post-discharge','clinical'),
('APT-005','MRN10005','Patricia Young','Maternity Follow-up','DEP-MAT','Maternity','2026-06-26','09:30','Dr Fatima Khan','Booked','Routine','clinical'),
('APT-006','MRN10006','Leo Bennett','Paediatric Review','DEP-PAED','Paediatrics','2026-06-26','13:30','Dr Omar Hussain','Booked','Urgent','clinical'),
('APT-007','MRN10007','Robert Green','Oncology Day Unit','DEP-ONC','Oncology','2026-06-27','08:30','Dr Emily Turner','Booked','Two-week wait','clinical'),
('APT-008','MRN10008','Linda Hughes','Radiology Contrast Review','DEP-RAD','Radiology','2026-06-27','15:00','Dr James Wilson','Booked','Routine','clinical')
ON CONFLICT (appointment_id) DO NOTHING;

INSERT INTO formulary VALUES
('MED-001','Vancomycin','Antibiotic',true,'Consultant approval and pharmacist verification','Per protocol by levels','Therapeutic drug monitoring','clinical'),
('MED-002','Gentamicin','Antibiotic',true,'Consultant approval and pharmacist verification','Dose by weight and renal function','Drug levels and renal function','clinical'),
('MED-003','Insulin infusion','Endocrine',true,'Two staff checks and protocol','Protocol dependent','Hourly glucose monitoring','clinical'),
('MED-004','Warfarin','Anticoagulant',true,'Prescriber and pharmacist verification','Dose by INR','INR monitoring','clinical'),
('MED-005','Paracetamol','Analgesic',false,'No special approval','1 g every 4-6 hours, max 4 g/day','Check combined products','all_staff'),
('MED-006','Salbutamol','Respiratory',false,'No special approval','Per inhaler or nebuliser protocol','Observe response','all_staff'),
('MED-007','Noradrenaline','Critical care',true,'ICU consultant approval','Protocol dependent','Continuous blood pressure monitoring','clinical'),
('MED-008','Meropenem','Antibiotic',true,'Microbiology or consultant approval','Dose by renal function','Renal function and cultures','clinical')
ON CONFLICT (medicine_id) DO NOTHING;
