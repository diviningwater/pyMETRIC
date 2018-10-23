# -*- coding: utf-8 -*-
"""
Created on Sat May 30 10:59:35 2015

@author: hector
"""
import numpy as np

import pyTSEB.meteo_utils as met
import pyTSEB.resistances as res
import pyTSEB.MO_similarity as MO
import pyTSEB.TSEB as tseb

#==============================================================================
# List of constants used in TSEB model and sub-routines
#==============================================================================
# Change threshold in  Monin-Obukhov lengh to stop the iterations
L_thres = 0.00001
# Change threshold in  friction velocity to stop the iterations
u_thres = 0.00001
# mimimun allowed friction velocity
u_friction_min = 0.01
# Maximum number of interations
ITERATIONS = 100
# kB coefficient
kB = 0.0
# Stephan Boltzmann constant (W m-2 K-4)
sb = 5.670373e-8
# von Karman constant
KARMAN = 0.41

TALL_REFERENCE = 1
SHORT_REFERENCE = 0

def METRIC(Tr_K,
           T_A_K,
           u,
           ea,
           p,
           Sn,
           L_dn,
           emis,
           z_0M,
           d_0,
           z_u,
           z_T,
           cold_pixel,
           hot_pixel,
           LE_cold,
           LE_hot=0,
           use_METRIC_resistance = True,
           calcG_params=[
                [1],
                0.35],
           UseL=False,
           UseDEM=False):
    
    '''Calulates bulk fluxes using METRIC model

    Parameters
    ----------
    Tr_K : float
        Radiometric composite temperature (Kelvin).
    T_A_K : float
        Air temperature (Kelvin).
    u : float
        Wind speed above the canopy (m s-1).
    ea : float
        Water vapour pressure above the canopy (mb).
    p : float
        Atmospheric pressure (mb), use 1013 mb by default.
    S_n : float
        Solar irradiance (W m-2).
    L_dn : float
        Downwelling longwave radiation (W m-2)
    emis : float
        Surface emissivity.
    albedo : float
        Surface broadband albedo.
    z_0M : float
        Aerodynamic surface roughness length for momentum transfer (m).
    d_0 : float
        Zero-plane displacement height (m).
    z_u : float
        Height of measurement of windspeed (m).
    z_T : float
        Height of measurement of air temperature (m).
    col_pixel : tuple
        pixel coordinates (row, col) for the cold endmember
    hot_pixel : tuple
        pixel coordinates (row, col) for the hot endmember
    calcG_params : list[list,float or array], optional
        Method to calculate soil heat flux,parameters.

            * [[1],G_ratio]: default, estimate G as a ratio of Rn_S, default Gratio=0.35.
            * [[0],G_constant] : Use a constant G, usually use 0 to ignore the computation of G.
            * [[2,Amplitude,phase_shift,shape],time] : estimate G from Santanello and Friedl with G_param list of parameters (see :func:`~TSEB.calc_G_time_diff`).
    UseL : Optional[float]
        If included, its value will be used to force the Moning-Obukhov stability length.

    Returns
    -------
    flag : int
        Quality flag, see Appendix for description.
    Ln : float
        Net longwave radiation (W m-2)
    LE : float
        Latent heat flux (W m-2).
    H : float
        Sensible heat flux (W m-2).
    G : float
        Soil heat flux (W m-2).
    R_A : float
        Aerodynamic resistance to heat transport (s m-1).
    u_friction : float
        Friction velocity (m s-1).
    L : float
        Monin-Obuhkov length (m).
    n_iterations : int
        number of iterations until convergence of L.
    
    References
    ----------
    
    '''

    # Convert input scalars to numpy arrays and check parameters size
    Tr_K = np.asarray(Tr_K)
    (T_A_K,
     u,
     ea,
     p,
     Sn,
     L_dn,
     emis,
     z_0M,
     d_0,
     z_u,
     z_T,
     calcG_array) = map(tseb._check_default_parameter_size,
                        [T_A_K,
                         u,
                         ea,
                         p,
                         Sn,
                         L_dn,
                         emis,
                         z_0M,
                         d_0,
                         z_u,
                         z_T,
                         calcG_params[1]],
                        [Tr_K] * 12)

    # Create the output variables
    [flag, Ln, LE, H, G, R_A] = [np.zeros(Tr_K.shape)+np.NaN for i in range(6)]

    # iteration of the Monin-Obukhov length
    if isinstance(UseL, bool):
        # Initially assume stable atmospheric conditions and set variables for
        L = np.zeros(Tr_K.shape) + np.inf
        max_iterations = ITERATIONS
    else:  # We force Monin-Obukhov lenght to the provided array/value
        L = np.ones(Tr_K.shape) * UseL
        max_iterations = 1  # No iteration
    
    if isinstance(UseDEM, bool):
        Tr_datum = np.asarray(Tr_K)
        Ta_datum = np.asarray(T_A_K)
    else:
        gamma_w = met.calc_lapse_rate_moist(T_A_K, ea, p)
        Tr_datum = Tr_K + gamma_w * UseDEM
        Ta_datum = T_A_K + gamma_w * (UseDEM + z_T)
        
    # Calculate the general parameters
    rho = met.calc_rho(p, ea, T_A_K)  # Air density
    c_p = met.calc_c_p(p, ea)  # Heat capacity of air
    # Calculate the general parameters
    rho_datum = met.calc_rho(p, ea, Ta_datum)  # Air density
 
    # Calc initial Monin Obukhov variables
    u_friction = MO.calc_u_star(u, z_u, L, d_0, z_0M)
    u_friction = np.maximum(u_friction_min, u_friction)

    z_0H = res.calc_z_0H(z_0M, kB=kB)

    # Calculate Net radiation
    Ln = emis * L_dn - emis * met.calc_stephan_boltzmann(Tr_K)
    Rn = np.asarray(Sn + Ln)

    # Compute Soil Heat Flux
    i = np.ones(Rn.shape, dtype=bool)
    G[i] = tseb.calc_G([calcG_params[0], calcG_array], Rn, i)

    # Get cold and hot variables
    endmembers = [[cold_pixel], [hot_pixel]]
 
    H_endmembers = calc_H_residual(Rn[endmembers], G[endmembers], LE=LE[endmembers])

    #==============================================================================
    #     HOT and COLD PIXEL ITERATIONS FOR MONIN-OBUKHOV LENGTH AND H TO CONVERGE
    #==============================================================================
    # Initially assume stable atmospheric conditions and set variables for
    L_old = np.ones(2)
    L_diff = np.ones(2) * float('inf')
    for iteration in range(max_iterations):
        if np.all(L_diff < L_thres):
            break

        if isinstance(UseL, bool):
            # Now L can be recalculated and the difference between iterations
            # derived
            L_endmembers = MO.calc_L(u_friction[endmembers], 
                                     T_A_K[endmembers], 
                                     rho_datum[endmembers], 
                                     c_p[endmembers], 
                                     H_endmembers, 
                                     np.asarray([LE_cold, LE_hot]))

            L_diff = np.fabs(L_endmembers - L_old) / np.fabs(L_old)
            L_old = np.array(L_endmembers)
            L_old[np.fabs(L_old) == 0] = 1e-36

            u_friction_endmembers = MO.calc_u_star(u[endmembers], 
                                                   z_u[endmembers], 
                                                   L_endmembers,
                                                   d_0[endmembers],
                                                   z_0M[endmembers])

            u_friction_endmembers = np.maximum(u_friction_min, u_friction_endmembers)

        else:
            u_friction_endmembers = u_friction[endmembers]

    # Hot and Cold aerodynamic resistances
    R_A_params = {"z_T": z_T[endmembers],
                  "u_friction": u_friction_endmembers,  
                  "L": L_endmembers,
                  "d_0": d_0[endmembers],
                  "z_0H": z_0H[endmembers]}
        
    R_A_endmembers, _, _ = tseb.calc_resistances(tseb.KUSTAS_NORMAN_1999, 
                                                 {"R_A": R_A_params})

    # Calculate the temperature gradients
    dT_endmembers = calc_dT(H[endmembers],
                            R_A_endmembers,
                            rho_datum[endmembers],
                            c_p[endmembers])

    #dT constants
    dT_a = (dT_endmembers[1] - dT_endmembers[0]) \
            / (Tr_datum[endmembers[1]] - Tr_datum[endmembers[0]])    #Allen 2007 eq 50
    
    dT_b = (dT_endmembers[1] - dT_a) / Tr_datum[endmembers[1]]                    #Allen 2007 eq 51
    
    #Apply the constant to the whole image
    dT = dT_a + dT_b * Tr_datum                         #Allen 2007 eq. 29
   
#==============================================================================
#     ITERATIONS FOR MONIN-OBUKHOV LENGTH AND H TO CONVERGE
#==============================================================================
    # Initially assume stable atmospheric conditions and set variables for
    L_old = np.ones(dT.shape)
    L_diff = np.ones(dT.shape) * float('inf')
    for n_iterations in range(max_iterations):
        if np.all(L_diff < L_thres):
            break
        
        if use_METRIC_resistance == True:
            R_A = calc_R_A_METRIC(u_friction, L)
        
        else:
            R_A = {"z_T": z_T,
                   "u_friction": u_friction,  
                   "L": L,
                   "d_0": d_0,
                   "z_0H": z_0H}
                
            R_A, _, _ = tseb.calc_resistances(tseb.KUSTAS_NORMAN_1999, 
                                              {"R_A": R_A_params})
        
        H = calc_H(dT, rho_datum, c_p, R_A) 
        LE = Rn - G - H
        
        if isinstance(UseL, bool):
            # Now L can be recalculated and the difference between iterations
            # derived
            L = MO.calc_L(u_friction, T_A_K, rho, c_p, H, LE)
                                     
            L_diff = np.fabs(L_endmembers - L_old) / np.fabs(L_old)
            L_old = np.array(L_endmembers)
            L_old[np.fabs(L_old) == 0] = 1e-36

            u_friction = MO.calc_u_star(u, 
                                        z_u, 
                                        L,
                                        d_0,
                                        z_0M)

            u_friction = np.maximum(u_friction_min, u_friction)


    flag, Ln, LE, H, G, R_A, u_friction, L, n_iterations = map(
        np.asarray, (flag, Ln, LE, H, G, R_A, u_friction, L, n_iterations))

    return flag, Ln, LE, H, G, R_A, u_friction, L, n_iterations


def calc_dT(H, R_AH, rho, c_p):
    
    dT = H * R_AH / (rho * c_p)
    return dT

    
def calc_G_Allen(Rn,LST,albedo,NDVI):
    '''Calculate soil heat flux
    
    Parameters
    ----------
    Rn : net radiation (W m-2)
    LST : Land Surface Temperature (Kelvin)
    albedo : surface broadband albedo
    NDVI : Normalized Difference Vegetation Index
    
    Returns
    -------
    G_flux : Soil heat flux (W m-2)
    
    Based on Allen 2007 eq. 26'''
    
    G_flux = Rn * (LST - 273.15) * (0.0038 + 0.0074*albedo) * (1 - 0.98*(NDVI**4))

    return G_flux

def calc_H(dT, rho , cp, R_AH):
    ''' Calculates the Sensible heat flux using the bulk transfer equation
    
    Parameters
    ----------
    dT : float or array
        gradient temperature
    rho : float or array
        Density of air
    cp : float or array
        heat capacity of air
    R_AH : float or array
        aerodynamic resistance to heat transport (s m-1)
    
    Returns
    -------
    H : float or array
        Sensible heat flux (W m-2)

    References
    ----------    
    based on Allen 2007 eq 28'''
    
    H = rho * cp * dT / R_AH

    return H
    
def calc_H_residual(Rn, G, LE=0.0):
    ''' Calculates the Sensible heat flux as residual of the energy balance
    
    Parameters
    ----------
    Rn : float or array
        net radiation (W m-2)
    G : float or array
        Soil heat flux (W m-2)
    LE : float or array
        latent heat flux (W m-2) default=0, for dry pixels
    
    Returns
    -------
    H : float or array
        Sensible heat flux (W m-2)
    
    '''
    H = Rn - G - LE
    return H

def pet_asce(T_A_K, u, ea, p, Sdn, z_u, z_T, f_cd = 1, reference = TALL_REFERENCE):
    '''Calcultaes the latent heat flux for well irrigated and cold pixel using
    ASCE potential ET from a tall (alfalfa) crop

    Parameters
    ----------
    T_K : float or array
        temperature (Kelvin)
    
    Returns
    -------    
    LE_cold : float or array
        latent heat flux for well irrigated pixel (W m-2)
    
    '''
    # Atmospheric constants
    delta = 10. * met.calc_delta_vapor_pressure(T_A_K)  # slope of saturation water vapour pressure in mb K-1
    lambda_ = met.calc_lambda(T_A_K)                     # latent heat of vaporization MJ kg-1
    psicr = met.calc_psicr(p, lambda_)                     # Psicrometric constant (mb K-1)
    es = met.calc_vapor_pressure(T_A_K)             # saturation water vapour pressure in mb

    # Net shortwave radiation
    Sdn = Sdn * 3600 / 1e6 # W m-2 to MJ m-2 h-1
    albedo = 0.23
    Sn = Sdn * (1.0 - albedo)    
    # Net longwave radiation
    Ln = sb * f_cd * (0.34 - 0.14 * np.sqrt(ea)) * T_A_K**4
    # Net radiation
    Rn = Sn + Ln
    # Soil heat flux
    if reference == TALL_REFERENCE:
        G_ratio = 0.04
        h_c = 0.5
        C_d = 0.25
        C_n = 66.0
        #R_s = 30.0
    else:
        G_ratio = 0.1
        h_c = 0.12
        C_d = 0.24
        C_n = 37.0
        #R_s = 50.0
    
    
    # Soil heat flux
    G = G_ratio * Rn
    # Windspeed at 2m height
    z_0M = h_c * 0.123
    d = h_c * 0.67
    u_2 = wind_profile(u, z_u, z_0M, 2.0, d)
    
    ET = (0.048 * delta * (Rn - G) + psicr * C_n * u_2 * (es - ea) / T_A_K) \
         / (delta + psicr * C_d * u_2)
    
    return ET

def calc_cloudiness(Sdn, S_0):
    
    f_cd = 1.35 * Sdn/S_0 - 0.35
    f_cd = np.clip(f_cd, 0.05, 1.0)    

def wind_profile(u, z_u, z_0M, d, z):
    
    u_z = u * np.log((z_u - d)/z_0M) / np.log((z - d)/z_0M)
    
    return u_z

def calc_R_A_METRIC(ustar, L):
    ''' Estimates the aerodynamic resistance to heat transport based on the
    MO similarity theory.

    Parameters
    ----------
    z_T : float
        air temperature measurement height (m).
    ustar : float
        friction velocity (m s-1).
    L : float
        Monin Obukhov Length for stability
    d_0 : float
        zero-plane displacement height (m).
    z_0M : float
        aerodynamic roughness length for momentum trasport (m).
    z_0H : float
        aerodynamic roughness length for heat trasport (m).

    Returns
    -------
    R_A : float
        aerodyamic resistance to heat transport in the surface layer (s m-1).

    References
    ----------
    .. .
    '''

    # Convert input scalars to numpy arrays
    ustar, L = map(np.asarray, (ustar, L))

    # if L -> infinity, z./L-> 0 and there is neutral atmospheric stability
    # other atmospheric conditions
    L[L == 0] = 1e-36
    Psi_H_2 = MO.calc_Psi_H(2.0 / L)
    Psi_H_01 = MO.calc_Psi_H(0.1 / L)

    #i = np.logical_and(z_star>0, z_T<=z_star)
    #Psi_H_star[i] = MO.calc_Psi_H_star(z_T[i], L[i], d_0[i], z_0H[i], z_star[i])
    i = ustar != 0
    R_A = np.asarray(np.ones(ustar.shape) * float('inf'))
    R_A[i] = (np.log(2.0 / 0.1) - Psi_H_2[i] + Psi_H_01[i]) / (ustar[i] * KARMAN)
    return np.asarray(R_A)
