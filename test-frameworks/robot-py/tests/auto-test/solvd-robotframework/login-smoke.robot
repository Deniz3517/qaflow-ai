*** Settings ***
Documentation     AI-generated Robot Framework smoke for Login · SportHub
Library           SeleniumLibrary

*** Variables ***
${URL}           http://localhost:3001/login.html
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
    Title Should Be    Login · SportHub
    [Teardown]    Close Test Browser

Heading Sporthub Is Visible
    [Setup]    Open Page Under Test
    Page Should Contain Element    tag=h1
    [Teardown]    Close Test Browser

Heading Sign In To Your Account Is Visible
    [Setup]    Open Page Under Test
    Page Should Contain Element    tag=h2
    [Teardown]    Close Test Browser

Button Login Is Visible
    [Setup]    Open Page Under Test
    Element Should Be Visible    id=login-button
    [Teardown]    Close Test Browser

Form Login-Form Has Expected Inputs
    [Setup]    Open Page Under Test
    Page Should Contain Element    id=email
    Page Should Contain Element    id=password
    [Teardown]    Close Test Browser

