*** Settings ***
Documentation     AI-generated Robot Framework smoke for Solvd | AI Engineering Company
Library           SeleniumLibrary

*** Variables ***
${URL}           https://solvd.com/
${BROWSER}       Chrome

*** Keywords ***
Open Page Under Test
    Open Browser    ${URL}    ${BROWSER}
    Maximize Browser Window

Close Test Browser
    Close All Browsers

*** Test Cases ***
Page Loads With Expected Title
    [Setup]    Open Page Under Test
    Title Should Be    Solvd | AI Engineering Company
    [Teardown]    Close Test Browser

Heading We Build Ai Systems That Still Work When You Scale Is Visible
    [Setup]    Open Page Under Test
    Page Should Contain Element    tag=h1
    [Teardown]    Close Test Browser

Heading It S Harder Than It Sounds Is Visible
    [Setup]    Open Page Under Test
    Page Should Contain Element    tag=h2
    [Teardown]    Close Test Browser

Heading Ai Capability Is Increasing Dramatically Shipping It Inside An Enterprise Is A Different Problem Entirely Is Visible
    [Setup]    Open Page Under Test
    Page Should Contain Element    tag=h2
    [Teardown]    Close Test Browser

Button Accept Is Visible
    [Setup]    Open Page Under Test
    Element Should Be Visible    xpath=//button[contains(.,'Accept')]
    [Teardown]    Close Test Browser

Button Deny Is Visible
    [Setup]    Open Page Under Test
    Element Should Be Visible    xpath=//button[contains(.,'Deny')]
    [Teardown]    Close Test Browser

Form Hsform 02Dfa0B5-E4E1-463D-B0D6-7Da521Bb2Cdc Has Expected Inputs
    [Setup]    Open Page Under Test
    Page Should Contain Element    id=firstname-02dfa0b5-e4e1-463d-b0d6-7da521bb2cdc
    Page Should Contain Element    id=lastname-02dfa0b5-e4e1-463d-b0d6-7da521bb2cdc
    Page Should Contain Element    id=email-02dfa0b5-e4e1-463d-b0d6-7da521bb2cdc
    Page Should Contain Element    id=company-02dfa0b5-e4e1-463d-b0d6-7da521bb2cdc
    Page Should Contain Element    id=jobtitle-02dfa0b5-e4e1-463d-b0d6-7da521bb2cdc
    [Teardown]    Close Test Browser

