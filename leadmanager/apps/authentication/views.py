from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login
from django.contrib import messages

from django.views import View


__author__ = 'Ricardo'
__version__ = '0.1'


class LoginView(View):

    def post(self, request):

        username = request.POST.get('username')
        password = request.POST.get('password')

        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            return redirect('app_prospects:dashboard')
        else:
            messages.error(request, 'Credenciales inválidas')
            return redirect('app_authentication:login')

    def get(self, request):

        if request.user.is_authenticated:
            return redirect('app_prospects:dashboard')

        return render(request, 'authentication/login.html')
